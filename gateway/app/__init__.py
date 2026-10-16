"""DevAgent gateway: control plane for the MacBook coding agent.

Public surface is served under ``/mobile`` (``https://deepseek-api.cs-hub.xyz/mobile``)
and contains three blocks:

* ``/mobile/api/**``   - REST + SSE consumed by the PWA (DevAgent-mobile)
* ``/mobile/agent/**``  - WebSocket control channel used by the MacBook agent
* ``/mobile/**``        - the built PWA (static assets)
"""

__version__ = "1.0.0"
