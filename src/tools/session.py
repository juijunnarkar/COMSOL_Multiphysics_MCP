"""Session management tools for COMSOL MCP Server."""

from typing import Optional
from mcp.server import Server
from mcp.server.fastmcp import FastMCP
import mph
import jpype


class SessionManager:
    """Singleton manager for COMSOL client session."""
    
    _instance: Optional["SessionManager"] = None
    _client: Optional[mph.Client] = None
    _models: dict[str, mph.Model] = {}
    _current_model: Optional[str] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def client(self) -> Optional[mph.Client]:
        return self._client
    
    @property
    def is_connected(self) -> bool:
        return self._client is not None
    
    @property
    def current_model(self) -> Optional[str]:
        return self._current_model
    
    @property
    def models(self) -> dict[str, mph.Model]:
        return self._models.copy()
    
    def start(self, cores: Optional[int] = None, version: Optional[str] = None) -> dict:
        """Start a COMSOL client session."""
        if self._client is not None:
            try:
                self._client.clear()
                self._models.clear()
                self._current_model = None
                return {
                    "success": True,
                    "version": self._client.version,
                    "cores": self._client.cores,
                    "standalone": self._client.standalone,
                    "message": "Cleared existing session and ready."
                }
            except Exception as e:
                return {"success": False, "error": f"Failed to clear existing session: {e}"}
        if jpype.isJVMStarted():
            return {
                "success": False,
                "error": "JVM already started (possibly from a previous failed attempt). Restart the MCP server or use comsol_connect(port=...) to connect to a running COMSOL server."
            }
        try:
            self._client = mph.Client(cores=cores, version=version)
            return {
                "success": True,
                "version": self._client.version,
                "cores": self._client.cores,
                "standalone": self._client.standalone,
            }
        except Exception as e:
            self._client = None
            return {
                "success": False,
                "error": str(e),
                "hint": "Standalone start failed. If a COMSOL server is running, use comsol_connect(port=...) instead."
            }
    
    def progress_log(self, file_path: str) -> dict:
        """Log solver progress to a file through ModelUtil.showProgress."""
        if self._client is None:
            return {"success": False, "error": "Not connected. Call comsol_connect first."}
        try:
            self._client.java.showProgress(str(file_path))
        except Exception as e:
            return {"success": False, "error": f"Could not start the progress log: {str(e).strip().splitlines()[0]}"}
        return {"success": True, "log_file": str(file_path)}

    def connect(self, port: int, host: str = "localhost") -> dict:
        """Connect to a remote COMSOL server."""
        if self._client is not None:
            # Check if the existing client is still alive
            try:
                _ = self._client.version
            except Exception:
                # Stale client — force reset
                self._client = None
                self._models.clear()
                self._current_model = None
            else:
                return {
                    "success": False,
                    "error": "COMSOL session already running. Call comsol_disconnect first."
                }
        if jpype.isJVMStarted():
            # JVM already up from a previous (possibly failed) attempt — reuse it
            try:
                import mph as _mph
                self._client = _mph.Client(port=port, host=host)
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "hint": "JVM is already running. If the previous connect failed, restart the MCP server process to fully reset state.",
                }
        else:
            try:
                self._client = mph.Client(port=port, host=host)
            except Exception as e:
                self._client = None
                return {"success": False, "error": str(e)}
        return {
            "success": True,
            "version": self._client.version,
            "port": port,
            "host": host,
        }
    
    def disconnect(self) -> dict:
        """Disconnect and clear the session."""
        if self._client is None:
            return {"success": True, "message": "No active session."}
        try:
            self._client.clear()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
        self._models.clear()
        self._current_model = None
        return {"success": True, "message": "Disconnected and session cleared."}
    
    def get_status(self) -> dict:
        """Get current session status."""
        if self._client is None:
            return {
                "connected": False,
                "message": "No active COMSOL session."
            }
        
        model_list = []
        for name in self._client.names():
            model_info = {"name": name}
            if name in self._models:
                model = self._models[name]
                model_info["file"] = model.file() if hasattr(model, 'file') else None
            model_list.append(model_info)
        
        return {
            "connected": True,
            "version": self._client.version,
            "cores": self._client.cores,
            "standalone": self._client.standalone,
            "models": model_list,
            "current_model": self._current_model,
        }
    
    def add_model(self, model: mph.Model) -> str:
        """Add a model to tracking."""
        name = model.name()
        self._models[name] = model
        if self._current_model is None:
            self._current_model = name
        return name
    
    def get_model(self, name: Optional[str] = None) -> Optional[mph.Model]:
        """Get a model by name or current model."""
        if name is None:
            name = self._current_model
        return self._models.get(name)
    
    def set_current_model(self, name: str) -> bool:
        """Set the current active model."""
        if name in self._models:
            self._current_model = name
            return True
        return False
    
    def remove_model(self, name: str) -> bool:
        """Remove a model from tracking and client."""
        if name in self._models and self._client is not None:
            try:
                self._client.remove(self._models[name])
                del self._models[name]
                if self._current_model == name:
                    self._current_model = next(iter(self._models.keys()), None)
                return True
            except Exception:
                pass
        return False


session_manager = SessionManager()


def register_session_tools(mcp: FastMCP) -> None:
    """Register session management tools with the MCP server."""
    
    @mcp.tool()
    def comsol_start(cores: Optional[int] = None, version: Optional[str] = None) -> dict:
        """
        Start a local COMSOL client session.
        
        Args:
            cores: Number of processor cores to use (default: all available)
            version: COMSOL version to use, e.g., '6.0' (default: latest installed)
        
        Returns:
            Session info including version and core count, or error message
        """
        return session_manager.start(cores=cores, version=version)
    
    @mcp.tool()
    def comsol_connect(port: int, host: str = "localhost") -> dict:
        """
        Connect to a remote COMSOL server.
        
        Args:
            port: Port number the COMSOL server is listening on
            host: Server hostname or IP address (default: 'localhost')
        
        Returns:
            Connection info or error message
        """
        return session_manager.connect(port=port, host=host)
    
    @mcp.tool()
    def comsol_disconnect() -> dict:
        """
        Disconnect from COMSOL and clear all models from memory.
        
        Returns:
            Success status and message
        """
        return session_manager.disconnect()
    
    @mcp.tool()
    def comsol_progress_log(file_path: str) -> dict:
        """
        Write solver progress to a file on this computer during every later solve.

        Read the file during a long solve to find a diverged run early. The
        COMSOL API method is ModelUtil.showProgress(filename).

        Args:
            file_path: Log file path

        Returns:
            The log file path, or an error
        """
        return session_manager.progress_log(file_path)

    @mcp.tool()
    def comsol_status() -> dict:
        """
        Get the current COMSOL session status.
        
        Returns:
            Session information including connection status, version, and loaded models
        """
        return session_manager.get_status()
