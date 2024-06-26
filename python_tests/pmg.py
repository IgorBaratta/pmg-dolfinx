from mpi4py import MPI
from dolfinx import mesh, fem, io
from dolfinx.fem import petsc
import ufl
from ufl import TestFunction, TrialFunction, inner, grad, div, Measure
import numpy as np
from petsc4py import PETSc
from cg import CGSolver
from chebyshev import Chebyshev
import basix


def boundary_condition(V):
    msh = V.mesh
    msh.topology.create_connectivity(msh.topology.dim - 1, msh.topology.dim)
    facets = mesh.exterior_facet_indices(msh.topology)
    dofs = fem.locate_dofs_topological(V, msh.topology.dim - 1, facets)
    return fem.dirichletbc(0.0, dofs, V)


def create_a(V, kappa, dx):
    u, v = TrialFunction(V), TestFunction(V)
    a = kappa * inner(grad(u), grad(v)) * dx
    return fem.form(a)


def create_L(V, kappa, u_e, dx):
    v = TestFunction(V)
    f = -kappa * div(grad(u_e))
    L = inner(f, v) * dx
    return fem.form(L)


def residual(b, A, u):
    r = A.createVecRight()
    A.mult(u.vector, r)
    r.axpby(1, -1, b)
    return r


def norm_L2(comm, v, measure=ufl.dx):
    return np.sqrt(
        comm.allreduce(
            fem.assemble_scalar(fem.form(ufl.inner(v, v) * measure)), op=MPI.SUM
        )
    )


def u_i(x):
    "Initial guess of solution"
    # omega = 2 * np.pi * 1
    # return np.sin(omega * x[0]) * np.sin(omega * x[1])
    return np.zeros_like(x[0])


def level_print(string, level):
    print(f'{(len(ks) - level) * "    "}{string}')


n = 10
ks = [1, 3]
num_iters = 10
kappa = 2.0
use_petsc = False
comm = MPI.COMM_WORLD
msh = mesh.create_unit_cube(MPI.COMM_WORLD, n, n, n, cell_type=mesh.CellType.hexahedron)

# Exact solution
x = ufl.SpatialCoordinate(msh)
u_e = ufl.sin(ufl.pi * x[0]) * ufl.sin(ufl.pi * x[1]) * ufl.sin(ufl.pi * x[2])

# FIXME Why does TP converge slower?
tensor_prod = True
family = basix.ElementFamily.P
variant = basix.LagrangeVariant.gll_warped
cell_type = msh.basix_cell()
# Function spaces
Vs = []
# Solutions
us = []
# Residuals
rs = []
# Corrections
dus = []
# Right-hand sides
bs = []
# Operators
As = []
# Boundary conditions
bcs = []
for i, k in enumerate(ks):
    if tensor_prod:
        basix_element = basix.create_tp_element(family, cell_type, k, variant)
        element = basix.ufl._BasixElement(basix_element)  # basix ufl element
        k_to_quad_deg = {1: 1, 2: 3, 3: 4}
        dx = Measure(
            "dx",
            metadata={"quadrature_rule": "GLL", "quadrature_degree": k_to_quad_deg[k]},
        )
    else:
        element = basix.ufl.element(family, cell_type, k, variant)
        dx = Measure("dx")

    V = fem.functionspace(msh, element)

    bc = boundary_condition(V)

    a = create_a(V, kappa, dx)
    A = petsc.assemble_matrix(a, bcs=[bc])
    A.assemble()

    # Assemble RHS
    L = create_L(V, kappa, u_e, dx)
    b = petsc.assemble_vector(L)
    petsc.apply_lifting(b, [a], bcs=[[bc]])
    petsc.set_bc(b, bcs=[bc])

    Vs.append(V)
    us.append(fem.Function(V))
    rs.append(fem.Function(V))
    dus.append(fem.Function(V))
    bs.append(b)
    As.append(A)
    bcs.append(bc)


# Create interpolation operators (needed to restrict the residual)
interp_ops = [petsc.interpolation_matrix(Vs[i], Vs[i + 1]) for i in range(len(Vs) - 1)]
for interp_op in interp_ops:
    interp_op.assemble()

# Create solvers
solvers = []

# Coarse
solver = PETSc.KSP().create(MPI.COMM_WORLD)
solver_prefix = "solver_0_"
solver.setOptionsPrefix(solver_prefix)
solver.setOperators(As[0])
solver.setType(PETSc.KSP.Type.PREONLY)
solver.pc.setType(PETSc.PC.Type.LU)
# FIXME Find best solver settings
opts = PETSc.Options()
# opts["help"] = None
# solver_options = {
#     "ksp_type": "cg",
#     "ksp_rtol": 1.0e-14,
#     "ksp_max_it": 60,
#     "pc_type": "hypre",
#     "pc_hypre_type": "boomeramg",
#     "pc_hypre_boomeramg_relax_type_down": "l1scaled-Jacobi",
#     "pc_hypre_boomeramg_relax_type_up": "l1scaled-Jacobi",
#     "pc_hypre_boomeramg_coarsen_type": "PMIS",
#     "pc_hypre_boomeramg_interp_type": "ext+i",
# }
# for key, val in solver_options.items():
#     opts[f"{solver_prefix}{key}"] = val
solver.setFromOptions()
solver.setUp()
solver.view()
solvers.append(solver)

# Fine
for i in range(1, len(ks)):
    if use_petsc:
        solver = PETSc.KSP().create(MPI.COMM_WORLD)
        solver_prefix = f"solver_{i}_"
        solver.setOptionsPrefix(solver_prefix)
        opts = PETSc.Options()
        smoother_options = {
            "ksp_type": "chebyshev",
            "esteig_ksp_type": "cg",
            "ksp_chebyshev_esteig_steps": 10,
            "ksp_max_it": 2,
            "ksp_initial_guess_nonzero": True,
            "pc_type": "jacobi",
            "ksp_chebyshev_kind": "first",
        }
        for key, val in smoother_options.items():
            opts[f"{solver_prefix}{key}"] = val
        solver.setOperators(As[i])
        solver.setFromOptions()
        solvers.append(solver)
    else:
        cg_solver = CGSolver(As[i], 20, 1e-6, jacobi=True, verbose=False)
        x = As[i].createVecRight()
        y = As[i].createVecRight()
        y.set(1.0)
        cg_solver.solve(y, x)
        est_eigs = cg_solver.compute_eigs()

        solvers.append(
            Chebyshev(
                As[i],
                2,
                (0.1 * est_eigs[-1], 1.1 * est_eigs[-1]),
                4,
                jacobi=True,
                verbose=False,
            )
        )

# Setup output files
u_files = [io.VTXWriter(msh.comm, f"u_{i}.bp", u, "bp4") for (i, u) in enumerate(us)]
r_files = [io.VTXWriter(msh.comm, f"r_{i}.bp", r, "bp4") for (i, r) in enumerate(rs)]
du_files = [
    io.VTXWriter(msh.comm, f"du_{i}.bp", du, "bp4") for (i, du) in enumerate(dus)
]

# Initial residual
r_norm_0 = residual(bs[-1], As[-1], us[-1]).norm()

# Interpolate initial guess
us[-1].interpolate(u_i)

# Main iteration loop
for iter in range(num_iters):
    # Start of iteration
    print(f"Iteration {iter + 1}:")

    # Zero initial guesses of errors?
    for u in us[:-1]:
        u.vector.set(0.0)

    # Sweep down the levels
    for i in range(len(ks) - 1, 0, -1):
        level_print(f"Level {i}:", i)
        level_print(
            f"    Initial:              residual norm = {(residual(bs[i], As[i], us[i])).norm()}",
            i,
        )
        # Smooth A_i u_i = b_i on fine level
        solvers[i].solve(bs[i], us[i].vector)

        # Compute residual r_i = b_i - A_i u_i
        rs[i].vector.array[:] = residual(bs[i], As[i], us[i])
        level_print(
            f"    After initial smooth: residual norm = {rs[i].vector.norm()}", i
        )
        r_files[i].write(iter)

        # Interpolate residual to next level
        interp_ops[i - 1].multTranspose(rs[i].vector, bs[i - 1])

    # Solve A_0 u_0 = r_0 on coarse level
    # FIXME Should this be done on other levels?
    petsc.set_bc(bs[0], bcs=[bcs[0]])
    solvers[0].solve(bs[0], us[0].vector)
    u_files[0].write(iter)
    level_print("Level 0:", 0)
    level_print(f"    residual norm = {(residual(bs[0], As[0], us[0])).norm()}", 0)
    level_print(f"    correction norm = {(us[0].vector).norm()}", 0)

    # Sweep up the levels
    for i in range(len(ks) - 1):
        level_print(f"Level {i + 1}:", i + 1)
        # Interpolate error to next level
        dus[i + 1].interpolate(us[i])

        level_print(f"    norm(us[{i}]) = {us[i].vector.norm()}", i + 1)
        level_print(f"    norm(dus[{i + 1}]) = {dus[i + 1].vector.norm()}", i + 1)

        du_files[i + 1].write(iter)

        # Add error to solution u_i += e_i
        us[i + 1].vector.array[:] += dus[i + 1].vector.array

        level_print(
            f"    After correction:     residual norm = {(residual(bs[i + 1], As[i + 1], us[i + 1])).norm()}",
            i + 1,
        )

        # Smooth on fine level A_i u_i = b_i
        solvers[i + 1].solve(bs[i + 1], us[i + 1].vector)

        level_print(
            f"    After final smooth:   residual norm = {(residual(bs[i + 1], As[i + 1], us[i + 1])).norm()}",
            i + 1,
        )

        u_files[i + 1].write(iter)

    # Compute relative residual norm
    r_norm = residual(bs[-1], As[-1], us[-1]).norm()
    print(f"\n    Relative residual norm = {r_norm / r_norm_0}")

    # Compute error in solution
    e_u = norm_L2(comm, u_e - us[-1])
    print(f"\n    L2-norm of error in u_1 = {e_u}\n\n")
