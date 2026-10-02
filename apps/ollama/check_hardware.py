import platform
import psutil
import sys

def get_size(bytes, suffix="B"):
    """
    Scale bytes to its proper format
    e.g:
        1253656 => '1.20MB'
        1253656678 => '1.17GB'
    """
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor

def check_hardware():
    print("="*40)
    print("Moltbot Ollama Hardware Detection")
    print("="*40)
    
    # System Info
    uname = platform.uname()
    print(f"System: {uname.system}")
    print(f"Machine: {uname.machine}")
    print(f"Processor: {uname.processor}")
    
    # CPU
    physical_cores = psutil.cpu_count(logical=False)
    total_cores = psutil.cpu_count(logical=True)
    print(f"Physical cores: {physical_cores}")
    print(f"Total cores: {total_cores}")
    
    # RAM
    svmem = psutil.virtual_memory()
    print(f"Total RAM: {get_size(svmem.total)}")
    print(f"Available RAM: {get_size(svmem.available)}")
    
    # Recommendations
    total_ram_gb = svmem.total / (1024**3)
    
    print("-" * 40)
    print("RECOMMENDATION:")
    
    if total_ram_gb < 4:
        print("⚠  Your system has less than 4GB of RAM.")
        print("   We recommend using very small quantized models like:")
        print("   - tinyllama")
        print("   - gemma:2b")
        print("   - phi3:3.8b (might be slow)")
    elif total_ram_gb < 8:
        print("ℹ  Your system has between 4GB and 8GB of RAM.")
        print("   You can run small to medium models:")
        print("   - llama3:8b (might be tight)")
        print("   - phi3:3.8b")
        print("   - gemma:7b (quantized)")
        print("   - mistral:7b")
    else:
        print("✅ Your system has 8GB+ RAM.")
        print("   You should be able to run most standard models comfortably:")
        print("   - llama3:8b")
        print("   - mistral:7b")
        print("   - gemma:7b")
        
    print("="*40)
    
    # We could output a JSON here if we wanted the bash script to parse it, 
    # but for now logging to stdout is good for the user to see in HA logs.

if __name__ == "__main__":
    try:
        check_hardware()
    except Exception as e:
        print(f"Error checking hardware: {e}")
