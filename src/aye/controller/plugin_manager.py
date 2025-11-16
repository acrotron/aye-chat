import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from rich import print as rprint
from aye.plugins.plugin_base import Plugin

DEBUG = False

class PluginManager:
    def __init__(self, tier: str = "free", verbose: bool = False) -> None:
        self.tier = tier
        self.verbose = verbose
        self.registry: Dict[str, Plugin] = {}

        if DEBUG:
            rprint(f"[bold yellow]Plugin Manager initialized with tier: {self.tier}[/]")

    def _load(self, file: Path):
        try:
            module_name = f"aye.plugins.{file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, file)
            if not spec or not spec.loader:
                return

            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
        
            for n, m in vars(mod).items():
                if isinstance(m, type) and issubclass(m, Plugin) and m is not Plugin:
                    plug = m()
                    if self._allowed(plug.premium):
                        plug.init({"verbose": self.verbose, "debug": DEBUG})
                        self.registry[plug.name] = plug
        except Exception as e:
            if self.verbose:
                rprint(f"[red]Failed to load plugin {file.name}: {e}[/]")

    def _allowed(self, plugin_tier: str) -> bool:
        # For now, all plugins are allowed
        return True

    def discover(self) -> None:
        # Plugins are now packaged, so we discover them relative to this file.
        plugin_dir = Path(__file__).parent.parent / "plugins"
        if not plugin_dir.is_dir():
            if self.verbose:
                rprint(f"[yellow]Plugin directory not found: {plugin_dir}[/]")
            return
        
        for f in plugin_dir.glob("*.py"):
            if f.name.startswith("__") or f.name == "plugin_base.py":
                continue
            self._load(f)

        if self.registry:
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
