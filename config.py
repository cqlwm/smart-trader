import os
import dotenv
import log

dotenv.load_dotenv()

logger = log.getLogger(__name__)

PROJECT_PATH = os.environ.get('PROJECT_PATH')
if not PROJECT_PATH:
    raise ValueError('PROJECT_PATH must be set')

DATA_PATH = f'{PROJECT_PATH}/data'

os.makedirs(DATA_PATH, exist_ok=True)

logger.info(f'PROJECT_PATH: {PROJECT_PATH}')
logger.info(f'DATA_PATH: {DATA_PATH}')

if __name__ == '__main__':
    logger.info('config')
