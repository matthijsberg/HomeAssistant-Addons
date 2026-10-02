# Ollama (Intel AI Core)

Run Ollama locally for private, high-performance AI. This add-on is optimized for Intel hardware, specifically **Intel Arc GPUs** and **Integrated GPUs (iGPU)** on Arrow Lake and newer platforms.

## Configuration

### Option: `model`
The model to run. You can choose from the list of popular models.
Default: `llama3.2:3b`

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

### Option: `gpu_overhead`
Reserve a portion of VRAM (in bytes) for other applications or system overhead.
- Default: `0`

### Option: `debug`
Enable debug logging for Ollama.
- Default: `false`

## Web UI

The add-on includes a built-in Chat UI.
- **Chat:** Interact with the loaded model directly.
- **Performance Metrics:** View token generation speed and load times.
- **Model Management:** See which model is currently loaded.

## Hardware Support

This add-on uses the Intel Compute Runtime (Level Zero) for hardware acceleration on Intel GPUs.
- **Intel Arc Graphics** (Discrete and Integrated)
- **Intel iGPU** (Arrow Lake, Meteor Lake, etc.)

## Models

You can find more details about available models on the [Ollama Library](https://ollama.com/library).
