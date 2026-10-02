# STAGE 1: Builder
FROM ubuntu:24.04 AS builder
RUN apt-get update && apt-get install -y curl gnupg2 cmake git build-essential golang
# Install Intel repositories for SYCL/oneAPI
RUN curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble client unified" > /etc/apt/sources.list.d/intel-gpu.list
# Add Intel oneAPI repository for MKL
RUN curl -fsSL https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB | gpg --dearmor -o /usr/share/keyrings/oneapi-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" > /etc/apt/sources.list.d/oneAPI.list
RUN apt-get update && apt-get install -y intel-oneapi-mkl-devel-2025.0 libvulkan-dev
# Build Ollama with multi-backend support
RUN git clone https://github.com/ollama/ollama.git /app
WORKDIR /app
# Enable both SYCL and Vulkan backends
RUN OLLAMA_BACKENDS=sycl,vulkan go build .

# STAGE 2: Optimized Runtime
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y jq curl clinfo mesa-vulkan-drivers gnupg2
RUN curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble client" > /etc/apt/sources.list.d/intel-gpu.list
# Add Intel oneAPI repository for MKL and DPC++ Runtime
RUN curl -fsSL https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB | gpg --dearmor -o /usr/share/keyrings/oneapi-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" > /etc/apt/sources.list.d/oneAPI.list
# Install runtime libraries
RUN apt-get update && apt-get install -y \
    intel-opencl-icd \
    intel-level-zero-gpu \
    libze1 \
    libvulkan1 \
    intel-oneapi-compiler-dpcpp-cpp-runtime \
    intel-oneapi-mkl-core \
    intel-oneapi-mkl-sycl
COPY --from=builder /app/ollama /usr/bin/ollama
COPY run.sh /run.sh
RUN chmod a+x /run.sh
ENTRYPOINT ["/run.sh"]
