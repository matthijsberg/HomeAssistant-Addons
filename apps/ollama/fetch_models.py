import urllib.request
import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")

def fetch_popular_models():
    print("Fetching popular models from Ollama Library...")
    url = "https://ollama.com/library"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch library: {e}")
        return []
    
    # Simple regex to find /library/model-name in hrefs
    pattern = r'href="/library/([^/"]+)"'
    models = re.findall(pattern, html)
    
    # Remove duplicates but keep order
    seen = set()
    unique_models = [x for x in models if not (x in seen or seen.add(x))]
    
    return unique_models

def update_config_string(models):
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: {CONFIG_PATH} not found.")
        return

    with open(CONFIG_PATH, 'r') as f:
        content = f.read()
    
    # Create the list of models with some common variants
    model_options = []
    
    # Known high-profile models and their common tags
    special_cases = {
        "gemma3": ["270m", "1b", "4b", "12b", "27b", "vision"],
        "llama3.2": ["1b", "3b"],
        "llama3.1": ["8b", "70b"],
        "phi3.5": ["latest"],
        "gemma2": ["2b", "9b", "27b"],
        "mistral": ["7b"]
    }

    for m in models:
        if m in special_cases:
            for v in special_cases[m]:
                model_options.append(f"{m}:{v}")
        else:
            model_options.append(m)
    
    model_options = sorted(list(set(model_options)))
    models_string = "|".join(model_options)
    
    # Use regex to replace the model list in schema
    # Search for model: "list(...)"
    new_schema_line = f'  model: "list({models_string})"'
    pattern = r'  model: "list\([^)]*\)"'
    
    if re.search(pattern, content):
        new_content = re.sub(pattern, new_schema_line, content)
        with open(CONFIG_PATH, 'w') as f:
            f.write(new_content)
        print(f"Updated {CONFIG_PATH} with {len(model_options)} model options.")
    else:
        print("Could not find the model list pattern in config.yaml.")

if __name__ == "__main__":
    popular_models = fetch_popular_models()
    if popular_models:
        # Ensure core ones are included
        core_models = ["llama3.2", "llama3.1", "gemma3", "phi3.5", "mistral-nemo"]
        for cm in core_models:
            if cm not in popular_models:
                popular_models.append(cm)
        
        update_config_string(popular_models)
    else:
        print("No models found via scraping. Check connection or URL.")
