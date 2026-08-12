"""Python entrypoint: boots the FastAPI app with uvicorn on PORT (default 8095)."""
import os

import uvicorn

from main import app  # noqa: F401  (imported so uvicorn can serve it)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8095")))
