import traceback
from dotenv import load_dotenv
import time
import os

from modules.difox import Difox
from modules import files as fs

load_dotenv()


def main():
    start_time = time.time()

    # Read Files
    file_directory = os.getenv('file_directory')
    if not file_directory:
        print('Please set your file_directory in the .env file.')
        exit()

    inventory_file = '{}/difox-italia.csv'.format(file_directory)
    updated_inventory_file = '{}/difox-italia-parse.csv'.format(file_directory)
    bad_product_file = '{}/italia-bad-products.csv'.format(file_directory)

    username = os.getenv('difox_user')
    password = os.getenv('difox_pass')
    if not username or not password:
        print('Please set your difox_user and difox_pass in the .env file.')
        exit()

    df = fs.read_sheet(inventory_file, dtype={'EAN': str})
    if ln := df.duplicated(subset=['EAN']).sum():
        print('Warning: {} duplicated EANs in the inventory file.'.format(ln))
    products = df.to_dict('records')


    # Difox
    difox = Difox(username, password, file_directory, test_env=(os.getenv('env_type') == 'test'),
                  headless=True, profile=os.getenv('chrome_profile'))  # type: ignore
    try:
        difox.handle_login()
        difox.update_products(
            products, updated_inventory_file, bad_product_file)
        
    except Exception:
        traceback.print_exc()
    finally:
        print('\nExecution time: {} min'.format(
            round((time.time() - start_time)/60, 2)))
        del difox


if __name__ == '__main__':

    main()
