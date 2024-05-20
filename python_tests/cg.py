from mpi4py import MPI
from dolfinx.mesh import exterior_facet_indices, create_unit_cube
from dolfinx.fem.petsc import (
    assemble_matrix,
    assemble_vector,
    apply_lifting,
    set_bc,
)
from dolfinx import fem, mesh
from ufl import TestFunction, TrialFunction, dx, inner, grad
from scipy import linalg
import numpy as np
from petsc4py import PETSc


class CGSolver:
    def __init__(self, A, max_iters, rtol, verbose=False) -> None:
        self.A = A
        self.max_iters = max_iters
        self.rtol = rtol
        self.verbose = verbose
        self.alphas = []
        self.betas = []

    def solve(self, b, x):
        r = b - self.A @ x
        p = r.copy()
        rnorm = r.dot(r)
        rnorm_0 = rnorm

        if self.verbose:
            print("num dofs = ", r.size)
            print(f"rnorm0 = {rnorm}")

        for i in range(self.max_iters):
            y = self.A @ p
            self.alphas.append(rnorm / (p.dot(y)))
            x += self.alphas[-1] * p
            r -= self.alphas[-1] * y
            rnorm_new = r.dot(r)
            self.betas.append(rnorm_new / rnorm)
            rnorm = rnorm_new
            p = self.betas[-1] * p + r

            if self.verbose:
                print(f"Iteration {i + 1}: residual {rnorm**(1 / 2)}")
                print(f"alpha = {self.alphas[-1]}")
                print(f"beta = {self.betas[-1]}")

            if np.sqrt(rnorm / rnorm_0) < self.rtol:
                break

    def compute_eigs(self):
        # Compute tridiagonal matrix (see Yousef Saad Iterative Methods ch:6.7.3)
        n_iters = len(self.alphas)
        trmat = np.zeros((n_iters, n_iters))
        for i in range(n_iters):
            trmat[i, i] = 1 / self.alphas[i]

        for i in range(1, n_iters):
            trmat[i, i] += self.betas[i - 1] / self.alphas[i - 1]
            trmat[i, i - 1] = np.sqrt(self.betas[i - 1]) / self.alphas[i - 1]
            trmat[i - 1, i] = np.sqrt(self.betas[i - 1]) / self.alphas[i - 1]

        eig_est = sorted(np.real(linalg.eigvals(trmat)))
        return (eig_est[0], eig_est[-1])


if __name__ == "__main__":
    # TODO Do the same with PETSc and compare
    np.set_printoptions(linewidth=200)
    comm = MPI.COMM_WORLD
    msh = create_unit_cube(comm, 10, 10, 10, cell_type=mesh.CellType.hexahedron)
    print(f"Num cells = {msh.topology.index_map(msh.topology.dim).size_global}")

    V = fem.functionspace(msh, ("CG", 1))
    print(f"NDOFS = {V.dofmap.index_map.size_global}")
    u, v = TestFunction(V), TrialFunction(V)
    k = 2.0

    a = k * inner(grad(u), grad(v)) * dx
    a = fem.form(a)

    def f_expr(x):
        dx = (x[0] - 0.5) ** 2
        dy = (x[1] - 0.5) ** 2
        return 1000 * np.exp(-(dx + dy) / 0.02)

    f = fem.Function(V)
    f.interpolate(f_expr)
    L = fem.form(inner(f, v) * dx)

    msh.topology.create_connectivity(msh.topology.dim - 1, msh.topology.dim)
    facets = exterior_facet_indices(msh.topology)
    dofs = fem.locate_dofs_topological(V, msh.topology.dim - 1, facets)
    bc = fem.dirichletbc(0.0, dofs, V)

    A = assemble_matrix(a, bcs=[bc])
    A.assemble()

    cg_solver = CGSolver(A, 30, 1e-6, True)
    x = A.createVecRight()
    y = A.createVecRight()
    y.set(1.0)
    cg_solver.solve(y, x)
    est_eigs = cg_solver.compute_eigs()
    print(f"Estimated min/max eigenvalues = {est_eigs}")

    # Compare eigs to numpy
    vals = np.real(linalg.eigvals(A[:, :]))
    print("Min/max eigenvalues = ", vals[0], vals[-1])

    # Compare to PETSc
    solver = PETSc.KSP().create(comm)
    solver_prefix = "solver_"
    solver.setOptionsPrefix(solver_prefix)
    opts = PETSc.Options()
    smoother_options = {
        "ksp_type": "cg",
        "pc_type": "none",
        "ksp_max_it": 30,
        "ksp_rtol": 1e-6,
        "ksp_initial_guess_nonzero": True,
    }
    for key, val in smoother_options.items():
        opts[f"{solver_prefix}{key}"] = val

    def monitor(ksp, its, rnorm):
        print("Iteration: {}, rel. residual: {}".format(its, rnorm))

    solver.setMonitor(monitor)
    solver.setNormType(solver.NormType.NORM_UNPRECONDITIONED)
    solver.setOperators(A)
    solver.setFromOptions()

    print("\n\nPETSc")
    x.set(0.0)
    solver.solve(y, x)
