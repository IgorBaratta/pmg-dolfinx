#ifdef ROCM_SMI
#include <rocm_smi/rocm_smi.h>
#endif

bool initialised = false;

static const std::map<rsmi_memory_type_t, const char *>
   kDevMemoryTypeNameMap = {
    {RSMI_MEM_TYPE_VRAM, "VRAM memory"},
    {RSMI_MEM_TYPE_VIS_VRAM, "Visible VRAM memory"},
    {RSMI_MEM_TYPE_GTT, "GTT memory"},
};

int initialise_rocm_smi()
{

  int return_code = 0;
#ifdef ROCM_SMI
  rsmi_status_t ret;

  ret = rsmi_init(0);
  if(ret == RSMI_STATUS_SUCCESS){
    initialised = true;
  }
  return_code = ret;
#endif
  return return_code;

}

int shutdown_rocm_smi()
{

  int return_code = 0;
#ifdef ROCM_SMI
  rsmi_status_t ret;
  if(initialised){    
    ret = rsmi_shut_down();
    return_code = ret;
  }
#endif
  return return_code;

}

uint32_t num_monitored_devices(){

  uint32_t num_monitored_devices = 0;

  if(initialised){
    rsmi_num_monitor_devices(&num_monitored_devices);
  }

  return num_monitored_devices;

}

void print_amd_gpu_memory_busy(char const *text)
{
  int rank;
#ifdef ROCM_SMI
  rsmi_status_t err;
  uint32_t mem_busy_percent;

  MPI_Comm_rank(MPI_COMM_WORLD, &rank);

  if(initialised){

  for(uint32_t i = 0; i < num_monitored_devices(); ++i) {
    err = rsmi_dev_memory_busy_percent_get(i, &mem_busy_percent);
    if (err != RSMI_STATUS_SUCCESS) {
      return;
    }
    std::cout << text << " MPI Rank " << rank << " GPU " << i << " Memory Busy %: " << mem_busy_percent << "\n";
  }

  }else{
    std::cout << "ROCm SMI not initialised\n";
  }

#endif
  return;
}

void print_amd_gpu_memory_used(char const *text)
{
  int rank;
#ifdef ROCM_SMI
  rsmi_status_t err;
  uint64_t usage;

  MPI_Comm_rank(MPI_COMM_WORLD, &rank);

  if(initialised){

    for(uint32_t i = 0; i < num_monitored_devices(); ++i) {
      std::cout << text << " MPI Rank " << rank << " GPU " << i << " memory used:";
      for (uint32_t mem_type = RSMI_MEM_TYPE_FIRST; mem_type <= RSMI_MEM_TYPE_LAST; ++mem_type) {
        err = rsmi_dev_memory_usage_get(i, static_cast<rsmi_memory_type_t>(mem_type), &usage);
        if (err != RSMI_STATUS_SUCCESS) {
          return;
        }
        std::cout << " " << kDevMemoryTypeNameMap.at(static_cast<rsmi_memory_type_t>(mem_type));
        std::cout << " " << usage;
      }
      std::cout << "\n";
    }

  }else{
    std::cout << "ROCm SMI not initialised\n";
  }

#endif
  return;

}

void print_amd_gpu_memory_total(char const *text)
{
  int rank;
#ifdef ROCM_SMI
  rsmi_status_t err;
  uint64_t total;

  MPI_Comm_rank(MPI_COMM_WORLD, &rank);

  if(initialised){

    for(uint32_t i = 0; i < num_monitored_devices(); ++i) {
      std::cout << text << " MPI Rank " << rank << " GPU " << i << " total memory:";
      for (uint32_t mem_type = RSMI_MEM_TYPE_FIRST; mem_type <= RSMI_MEM_TYPE_LAST; ++mem_type) {
        err = rsmi_dev_memory_total_get(i, static_cast<rsmi_memory_type_t>(mem_type), &total);
        if (err != RSMI_STATUS_SUCCESS) {
          return;
        }
        std::cout << " " << kDevMemoryTypeNameMap.at(static_cast<rsmi_memory_type_t>(mem_type));
        std::cout << " " << total;
      }
      std::cout << "\n";
    }

  }else{
    std::cout << "ROCm SMI not initialised\n";
  }

#endif
  return;
}

void print_amd_gpu_memory_percentage_used(char const *text)
{
  int rank;
#ifdef ROCM_SMI
  rsmi_status_t err;
  uint64_t total;
  uint64_t usage;

  MPI_Comm_rank(MPI_COMM_WORLD, &rank);

  if(initialised){

    for(uint32_t i = 0; i < num_monitored_devices(); ++i) {
      std::cout << text << " MPI Rank " << rank << " GPU " << i << "  % memory used:";
      for (uint32_t mem_type = RSMI_MEM_TYPE_FIRST; mem_type <= RSMI_MEM_TYPE_LAST; ++mem_type) {
        err = rsmi_dev_memory_total_get(i, static_cast<rsmi_memory_type_t>(mem_type), &total);
        if (err != RSMI_STATUS_SUCCESS) {
          return;
        }
	err = rsmi_dev_memory_usage_get(i, static_cast<rsmi_memory_type_t>(mem_type), &usage);
        if (err != RSMI_STATUS_SUCCESS) {
          return;
        }


        std::cout << " " << kDevMemoryTypeNameMap.at(static_cast<rsmi_memory_type_t>(mem_type));
        std::cout << " " << (static_cast<float>(usage)*100)/total;
      }
      std::cout << "\n";
    }

  }else{
    std::cout << "ROCm SMI not initialised\n";
  }

#endif
  return;
}
