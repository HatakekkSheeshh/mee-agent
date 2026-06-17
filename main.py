import os
import uvicorn

try:
    from dotenv import load_dotenv
    load_dotenv(override=True, interpolate=False)
except ImportError:
    pass

from src.app import create_app

output_dir = os.path.join(os.path.dirname(__file__), "output")
app = create_app(output_dir=output_dir)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
