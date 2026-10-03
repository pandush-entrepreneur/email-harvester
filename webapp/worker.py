from app import init_db, worker_loop


if __name__ == "__main__":
    init_db()
    worker_loop()
