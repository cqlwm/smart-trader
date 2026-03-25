import log
import dotenv
import uvicorn
import sys

dotenv.load_dotenv()

logger = log.getLogger(__name__)

def main():
    if "--no-api" in sys.argv:
        from bot_manager import bot_manager
        bot_manager.start_bot()
    else:
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == '__main__':
    main()
