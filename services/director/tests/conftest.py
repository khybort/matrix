import os

os.environ.setdefault("SHARED_DATABASE_URL", "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared")
os.environ.setdefault("LOCAL_DATABASE_URL", "postgres://matrix:matrix_dev_only@localhost:5432/matrix")
