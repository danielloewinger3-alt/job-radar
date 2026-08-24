from backend.sources import adzuna, greenhouse, lever, reed, remoteok, usajobs

# Ordered roughly by signal quality / speed. Greenhouse, Lever and RemoteOK need
# no API key and work immediately; the rest activate once keys are set in .env.
SOURCES = [
    ("greenhouse", greenhouse.fetch),
    ("lever", lever.fetch),
    ("remoteok", remoteok.fetch),
    ("adzuna", adzuna.fetch),
    ("reed", reed.fetch),
    ("usajobs", usajobs.fetch),
]
