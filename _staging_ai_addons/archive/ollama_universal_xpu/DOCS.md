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

This add-on uses the Intel IPEX-LLM container for hardware acceleration on Intel GPUs.
- **Docker Hub:** [intelanalytics/ipex-llm-inference-cpp-xpu](https://hub.docker.com/r/intelanalytics/ipex-llm-inference-cpp-xpu)
- **Documentation:** [IPEX-LLM Documentation](https://github.com/intel-analytics/ipex-llm)
- **Ollama:** [Ollama Website](https://ollama.com)
