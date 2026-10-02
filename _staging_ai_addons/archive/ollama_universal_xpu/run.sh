#!/bin/bash

# Helper function for logging
log_info() {
    echo "[INFO] $1"
}

log_err() {
    echo "[ERROR] $1" >&2
}

echo "
  ██████╗ ██╗     ██╗      █████╗ ███╗   ███╗ █████╗
 ██╔═══██╗██║     ██║     ██╔══██╗████╗ ████║██╔══██╗
 ██║   ██║██║     ██║     ███████║██╔████╔██║███████║
 ██║   ██║██║     ██║     ██╔══██║██║╚██╔╝██║██╔══██║
 ╚██████╔╝███████╗███████╗██║  ██║██║ ╚═╝ ██║██║  ██║
  ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝
                                                     
  Universal XPU Edition (Intel/NVIDIA/AMD/CPU)
  Maintained by: Matthijs van den Berg
"

log_info "--- OLLAMA UNIVERSAL ACCELERATION STARTUP ---"

# 0. Initialize Intel oneAPI Environment (if present)
if [ -f /opt/intel/oneapi/setvars.sh ]; then
    log_info "Sourcing Intel oneAPI environment variables..."
    source /opt/intel/oneapi/setvars.sh
    
    # Explicitly add Intel libs to LD_LIBRARY_PATH to ensure visibility
    export LD_LIBRARY_PATH=/opt/intel/oneapi/compiler/latest/linux/lib:/opt/intel/oneapi/mkl/latest/lib/intel64:$LD_LIBRARY_PATH

    # Create symlinks for oneAPI libraries in /usr/lib to ensure dynamic linker finds them
    log_info "Creating symlinks for oneAPI libraries in /usr/lib..."
    ln -sf /opt/intel/oneapi/compiler/latest/linux/lib/libsycl.so /usr/lib/libsycl.so
    ln -sf /opt/intel/oneapi/compiler/latest/linux/lib/libsycl.so.7 /usr/lib/libsycl.so.7
    
    MKL_PATH="/opt/intel/oneapi/mkl/latest/lib/intel64"
    if [ -d "$MKL_PATH" ]; then
        ln -sf "$MKL_PATH"/libmkl_sycl.so /usr/lib/libmkl_sycl.so
        ln -sf "$MKL_PATH"/libmkl_intel_ilp64.so /usr/lib/libmkl_intel_ilp64.so
        ln -sf "$MKL_PATH"/libmkl_sequential.so /usr/lib/libmkl_sequential.so
        ln -sf "$MKL_PATH"/libmkl_core.so /usr/lib/libmkl_core.so
    fi
fi

# Create symlinks for Level Zero libraries
if [ -d /usr/lib/x86_64-linux-gnu ]; then
    log_info "Creating symlinks for Level Zero libraries in /usr/lib..."
    ln -sf /usr/lib/x86_64-linux-gnu/libze_loader.so.1 /usr/lib/libze_loader.so.1
    ln -sf /usr/lib/x86_64-linux-gnu/libze_loader.so.1 /usr/lib/libze_loader.so
    ln -sf /usr/lib/x86_64-linux-gnu/libze_intel_gpu.so.1 /usr/lib/libze_intel_gpu.so.1
fi

# 1. Hardware Enumeration & Requirement Check
if [ -f /etc/os-release ]; then
    UBUNTU_VER=$(grep "VERSION_ID" /etc/os-release | cut -d= -f2 | xargs)
else
    UBUNTU_VER="Unknown"
fi
KERNEL_VER=$(uname -r)
log_info "Host Environment -> OS: Ubuntu ${UBUNTU_VER}, Kernel: ${KERNEL_VER}"

# Debug: Check device nodes
log_info "--- Debug: /dev/dri Listing ---"
ls -l /dev/dri
log_info "--- Debug: clinfo Output ---"
clinfo
log_info "--- Debug: Library Locations ---"
ls -l /usr/lib/x86_64-linux-gnu/libze* 2>/dev/null
ls -l /usr/lib/libze* 2>/dev/null
log_info "No libze found in /usr/lib/" # Fallback message if ls fails
log_info "Searching for libsycl.so..."
find / -name libsycl.so* 2>/dev/null
log_info "Searching for setvars.sh..."
find / -name setvars.sh 2>/dev/null
log_info "--- End Debug ---"

# 2. Dynamic Backend Configuration
CONFIG_PATH="/data/options.json"
ACCEL_CHOICE=$(jq -r '.accelerator' $CONFIG_PATH)
GPU_IDX=$(jq -r '.gpu_index' $CONFIG_PATH)
log_info "Configuring for ${ACCEL_CHOICE} (Index: ${GPU_IDX})..."

case $ACCEL_CHOICE in
  "gpu")
    # Intel-specific SYCL/oneAPI Path
    # Force Level Zero backend for Intel GPU
    export ONEAPI_DEVICE_SELECTOR="level_zero:${GPU_IDX}"
    export OLLAMA_NUM_GPU=999
    ;;
  "npu")
    # Intel-specific NPU Path
    export ONEAPI_DEVICE_SELECTOR="level_zero:npu"
    export OLLAMA_NUM_GPU=999
    if [ ! -e "/dev/accel/accel0" ]; then
        log_err "❌ NPU device node missing. Check HAOS kernel version (needs 6.12+)."
    fi
    ;;
  "vulkan")
    # Generic Cross-Vendor Path (NVIDIA/AMD/Legacy Intel)
    export OLLAMA_VULKAN=1
    export GGML_VK_VISIBLE_DEVICES="${GPU_IDX}"
    export OLLAMA_NUM_GPU=999
    log_info "Using experimental Vulkan backend for generic GPU support."
    ;;
  "cpu")
    export OLLAMA_NUM_GPU=0
    ;;
esac

# 3. Global Runtime Tuning
export ZES_ENABLE_SYSMAN=1
export SYCL_CACHE_PERSISTENT=1
export OLLAMA_KEEP_ALIVE=$(jq -r '.keep_alive' $CONFIG_PATH)

log_info "Launching Ollama Server..."
exec /usr/bin/ollama serve
