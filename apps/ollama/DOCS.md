# Ollama (Local LLM)

Run Ollama locally for private, high-performance AI. This add-on is optimized for Intel hardware, including **Integrated GPUs (iGPU)** and NPUs.

## Configuration

### Option: `model`
The model to run. You can choose from the list or specify a custom model.
Default: `llama3:8b`

### Option: `device_type`
The hardware accelerator to use.
- `NPU`: Intel Neural Processing Unit (Core Ultra)
- `iGPU`: Intel Integrated GPU (Iris Xe, Arc Graphics)
- `GPU`: Discrete Intel Arc GPU
- `CPU`: Fallback if no accelerator is found

**Note:** This add-on supports Intel Integrated GPUs (iGPU) found in many modern Intel processors. Select `iGPU` or `GPU` to enable acceleration.

### Option: `keep_alive`
Controls how long the model stays loaded in memory (RAM/VRAM) after the last request.
- Default: `5m` (5 minutes)
- Set to `-1` to keep the model loaded indefinitely (improves performance for frequent requests but uses more RAM).
- Examples: `10m`, `1h`, `24h`.

### Option: `num_parallel`
The maximum number of parallel requests to handle.
- Default: `1`
- Increase this if you want to handle multiple requests simultaneously (requires more VRAM).

### Option: `max_loaded_models`
The maximum number of models to keep loaded in memory at the same time.
- Default: `1`
- Increase this if you have enough VRAM and want to switch between models quickly without reloading.

### Option: `num_ctx`
The default context window size (in tokens).
- Default: `2048`
- Increasing this allows for longer conversations but uses significantly more VRAM.

### Option: `debug`
Enable debug logging for Ollama.
- Default: `false`

## Web UI

The add-on includes a built-in Chat UI.
- **Performance Metrics:** View token generation speed and load times.
- **Model Management:** See which model is currently loaded.

## Hardware Support

This add-on is built on top of Intel IPEX-LLM and supports:
- Intel Core Ultra Processors (Series 1 and 2) with NPU
- Intel Arc Graphics (Discrete and Integrated)
- Intel Iris Xe Graphics

## Technical Details

**Base Image:** `intelanalytics/ipex-llm-inference-cpp-xpu:latest`

> **Note:** The Intel IPEX-LLM base image used by this add-on is no longer actively maintained by Intel. This may result in outdated Ollama versions and lack of support for newer model architectures (e.g., GLM-4). For the latest updates and broader model support, consider using the `ollama_intel` add-on which builds from a fresh Ubuntu base.

This add-on uses the Intel IPEX-LLM container for hardware acceleration on Intel GPUs.
- **Docker Hub:** [intelanalytics/ipex-llm-inference-cpp-xpu](https://hub.docker.com/r/intelanalytics/ipex-llm-inference-cpp-xpu)
- **Documentation:** [IPEX-LLM Documentation](https://github.com/intel-analytics/ipex-llm)
- **Ollama:** [Ollama Website](https://ollama.com)
