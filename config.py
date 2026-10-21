"""
Central configuration for the COMSOL MCP Server.

All values can be overridden via environment variables or a .env file.
See .env.example for a template.
"""
import os
from pathlib import Path

# COMSOL mphserver connection
COMSOL_PORT = int(os.getenv("COMSOL_PORT", "2036"))

# Project directories
BASE_DIR = Path(os.getenv("BASE_DIR", Path(__file__).parent))
MODELS_DIR = BASE_DIR / "comsol_models"
KNOWLEDGE_DIR = BASE_DIR / "knowledge_base"
PDF_DIR = BASE_DIR / "pdf"

# COMSOL binary (platform-dependent)
COMSOL_BIN = os.getenv(
    "COMSOL_BIN",
    "/Applications/COMSOL64/Multiphysics/bin/comsol"  # macOS default
)

# Recipe and knowledge base files
RECIPE_FILE = os.getenv("RECIPE_FILE", str(BASE_DIR / "comsol_recipes.json"))
KB_FILE = os.getenv("KB_FILE", str(BASE_DIR / "comsol_knowledge_base.json"))
