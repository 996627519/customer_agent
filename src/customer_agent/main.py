"""Uvicorn entry point."""

import uvicorn

from customer_agent.config import settings
from customer_agent.web import create_app


def run():
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, proxy_headers=True)


app = create_app()


if __name__ == "__main__":
    run()
