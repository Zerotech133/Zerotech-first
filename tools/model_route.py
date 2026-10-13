import json
import os

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "ollama_model_route.json")

def load_route_config(path=DEFAULT_PATH) -> dict:
    """
    Load the model route configuration from the specified path.
    
    :param path: The path to the configuration file.
    :return: A dictionary containing the configuration data.
    :raises FileNotFoundError: If the configuration file is not found.
    """
    try:
        with open(path, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found at {path}")

def select_model(task_type, config=None) -> str:
    """
    Select the model based on the task type and the provided configuration.
    
    :param task_type: The type of task for which to select a model.
    :param config: The configuration dictionary. If None, the default configuration is used.
    :return: The selected model name.
    """
    if config is None:
        config = load_route_config()
    
    for rule in config.get("route_rules", []):
        if rule.get("task_type") == task_type:
            return rule.get("model")
    
    return config.get("default_model")

def fallback_for(model, config=None) -> list:
    """
    Return the fallback chain for a given model based on the provided configuration.
    
    :param model: The model for which to find fallbacks.
    :param config: The configuration dictionary. If None, the default configuration is used.
    :return: A list of fallback models.
    """
    if config is None:
        config = load_route_config()
    
    fallback_chain = config.get("fallback_chain", {}).get(model, [])
    return fallback_chain
