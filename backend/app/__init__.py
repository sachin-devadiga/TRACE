"""PS-01 financial auditing agent package."""

from app.langgraph_compat import install_ormsgpack_compat

# LangGraph's checkpoint serializer imports ormsgpack at module import time.
# Install the local fallback before any application module imports LangGraph.
install_ormsgpack_compat()
