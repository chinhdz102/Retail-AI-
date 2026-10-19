"""Keep the default test suite isolated from developer service configuration."""

import os

# The default suite is an offline/system test. A developer's .env may point at
# PostgreSQL, but service integration is tested by explicit evaluation scripts.
os.environ["CATALOG_REPOSITORY"] = "demo"
# Local developer keys must not change fallback paths or appear in test traffic.
os.environ["GROQ_API_KEY"] = ""
os.environ["GROQ_API_KEY_FALLBACK_1"] = ""
os.environ["GROQ_API_KEY_FALLBACK_2"] = ""
os.environ["GROQ_API_KEY_FALLBACK_3"] = ""
os.environ["GROQ_API_KEY_FALLBACK_4"] = ""
