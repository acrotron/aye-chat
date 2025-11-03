import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from rich import print as rprint
from .plugin_base import Plugin

PLUGIN_ROOT = Path.home() / ".aye" / "plugins" #Removed to package plugins with the app
PATH_ROOT = Path.home() / ".aye" #Removed to package plugins with the app

class PluginManager:
    def __init__(self, tier: str = "free", verbose: bool = False) -> None:
        self.tier = tier
        self.verbose = verbose
        #self.registry: Dict[str, Plugin] = {}

        if (self.verbose):
            rprint(f"[bold yellow]Plugin Manager initialized with tier: {self.tier}[/]")
            rprint(f"[bold yellow]Plugin directory: {PLUGIN_ROOT}[/]")

        self.registry = {}


    def _load(self, file: Path):
        #print(file)

        # Get the full module name including package path
        #module_name = f"plugins.{file.stem}"
        module_name = f"{file.stem}"
    
        spec = importlib.util.spec_from_file_location(module_name, file)
        mod = importlib.util.module_from_spec(spec)

        #print(f"spec: {spec}")
        #print(f"module_name: {mod}")
    
        # Set the module name to include package context
        mod.__name__ = module_name
        mod.__package__ = "aye.plugins" #Updated to reflect new package structure
    
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    
        for n, m in vars(mod).items():
            if isinstance(m, type) and n.endswith("Plugin") and n != "Plugin":
                #print("--------------")
                #print(f"MARK 2: Module name1: {n}")
                #print(f"MARK 2: Module module: {m.__module__}")
                #print(f"MARK 2: Module name: {m.__name__}")
                #print(f"MARK 2: Module base: {m.__bases__}")
                #print(f"MARK 2: Module MRO: {m.__mro__}")
                #print(f"MARK 2: {isinstance(m, Plugin)}")
                plug = m()
                if self._allowed(plug.premium):
                    plug._init({"verbose": self.verbose})
                    self.registry[plug.name] = plug
                    

            continue
                
        #print(f"PLUGIN: Module module: {Plugin.__module__}")
        #print(f"PLUGIN: Module name: {Plugin.__name__}")
        #print(f"PLUGIN: Module base: {Plugin.__bases__}")
        #print(f"PLUGIN: Module MRO: {Plugin.__mro__}")
        #print(f"PLUGIN: {isinstance(m, Plugin)}")


    def _allowed(self, plugin_tier: str) -> bool:
        order = ["free", "pro", "team", "enterprise"]
        #return order.index(self.tier) >= order.index(plugin_tier)
        return True

    def discover(self) -> None:
        #Updated plugin discovery to use the package
        plugin_dir = Path(__file__).parent / "plugins" #Plugins are now in the same directory as the module.
        if not plugin_dir.is_dir():
            return
        for f in plugin_dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            self._load(f)

        #for k, v in self.registry.items():
        #    rprint(f"[bold cyan]{k}: {v}")
        plugins = ", ".join(self.registry.keys())
        rprint(f"[bold cyan]Plugins loaded: {plugins}[/]")


    def all(self) -> List[Plugin]:
        return list(self.registry.values())

    def handle_command(self, command_name: str, params: Dict[str, Any] = {}) -> Optional[Dict[str, Any]]:
        """Let plugins handle a command, return the first non-None response."""
        for plugin in self.all():
            response = plugin.on_command(command_name, params)
            if response is not None:
                return response
        return None
