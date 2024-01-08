from typing import Tuple
import pandas as pd
from tqdm import tqdm
import os
from concurrent.futures import ThreadPoolExecutor

from modules.warp import SeleniumWrap
from modules import files as fs
from modules import utility


class Difox():

    def __init__(self, username, password, file_directory: str, test_env: bool = False, headless: bool = False, profile: str = ''):
        self.username = username
        self.password = password
        self.file_directory = file_directory
        self.test_env = test_env

        self.se = SeleniumWrap()
        driver = self.se.setup_driver(headless=headless, profile=profile)
        assert driver, 'Failed to setup chrome'

        # Visit Home page
        login_url = 'https://www.difox.com/en'
        self.se.get_page(login_url)
        self.se.wait_random_time(2, 3)

        # Accept cookies
        self.accept_all_cookies()
        self.se.wait_random_time(2, 3)

    def accept_all_cookies(self):
        self.se.find_element_by_visible_text(
            'span', 'Accept all cookies', click=True, print_error=False)

    def handle_login(self):
        is_logged_in_selector = '.loginBox__badge'

        # Check if already logged in
        if self.se.find_element(is_logged_in_selector, print_error=False, timeout=2):
            print('Already logged in.')
            return True

        # Login form
        self.se.find_element('.loginBox__iconWrapper', click=True)
        user = self.se.find_element('#login-username')
        assert user, 'Login form not found'
        if user.get_attribute('value') != self.username:
            self.se.element_send_keys(self.username, '#login-username')
            self.se.element_send_keys(self.password, '#login-password')
        self.se.find_element_by_visible_text('button', 'Login', click=True)
        self.se.wait_random_time(5, 6)

        # Check if login successful
        if self.se.find_element(is_logged_in_selector):
            print('Login successful.')
            return True

        print('Login failed.')
        return False

    def scrape_product(self, url: str, add_cookies=True) -> Tuple[dict, str]:
        product = {}
        err = ''

        try:
            soup = self.se.get_page_by_requests(
                url, add_cookies=add_cookies, print_error=False)
            assert soup, 'Product page not found'

            # Session Timeout check
            msg = soup.select_one('.layout__flashMessageWrapper')
            if msg and msg.text.strip() == 'Your session ran out. Please login again.':
                if not self.handle_login():
                    print('Cannot Login.')
                    exit()

            # Product name
            title = soup.select_one('.productDisplay__title')
            assert title, 'Product name not found'
            product_name = title.text.strip()

            # Product EAN
            meta = soup.select_one('.is--eanCode')
            assert meta, 'Product EAN not found'
            product_ean = meta.text.replace('EAN code:', '').strip()

            # Product price
            price_div = soup.select_one('.price__price')
            assert price_div, 'Product price not found'
            price = utility.price_float(price_div.text.strip())

            # Product quantity
            stock_text = soup.select_one('.is--available')
            quantity = 5 if stock_text else 0

            product = {
                'EAN': product_ean,
                'product_name': product_name,
                'price': price,
                'quantity': quantity,
                'url': url
            }
        except AssertionError as e:
            err = str(e)

        return product, err

    def update_product(self, product):
        new_product, err = self.scrape_product(product['url'])
        if err:
            return {
                'EAN': product['EAN'],
                'error': err,
                'url': product['url']
            }, err
        else:
            return {
                'EAN': product['EAN'],
                'price': new_product['price'],
                'quantity': new_product['quantity'],
            }, None

    def update_products(self, products: list[dict], updated_inventory_file: str, bad_product_file: str) -> list[dict]:
        if self.test_env:
            print('Test mode: Only 10 products updating.')
            products = products[:10]

        print('\nProduct Availability and Price Update:')
        print('Inventory size: {} Products'.format(len(products)))

        updated_products = []
        bad_products = []

        with ThreadPoolExecutor(max_workers=15) as executor:
            for new_product, err in tqdm(executor.map(self.update_product, products), desc='Updating', colour='yellow', total=len(products)):
                if err:
                    bad_products.append(new_product)
                else:
                    updated_products.append(new_product)

        df = pd.DataFrame(updated_products)
        fs.write_to_sheet(df, updated_inventory_file)
        fs.write_to_sheet(pd.DataFrame(bad_products), bad_product_file)

        print('Updated: {}, In stock: {}, Out of stock: {}, Bad Product: {}'.format(
            len(df), df['quantity'].value_counts().get(5), df['quantity'].value_counts().get(0), len(bad_products)))
        return updated_products

    def product_urls_by_catalogue(self, catalog_url: str) -> list[str]:
        product_urls = []

        try:
            soup = self.se.get_page(catalog_url)
            assert soup, 'Catalog page not found'

            result_txt = self.se.find_element('.displayProducts__number')
            assert result_txt, 'Total result text not found in catalog {}'.format(
                catalog_url)
            # print('Catalog Total Product:', result_txt.text)

            # Set per page 100 item
            self.se.find_element(
                '.displayProducts__perPage-select button', click=True)
            self.se.find_element('button[aria-posinset="4"]', click=True)
            self.se.wait_random_time(2, 3)
            assert 'pageSize=100' in self.se.driver.current_url, 'Failed to set per page 100 item'

            # Pagination
            ul = self.se.find_element('ul.pagination')
            assert ul is not None, 'Pagination not found'
            lis = self.se.find_elements('li', parent=ul)

            page = len(lis) - 2
            for _ in range(1, page+1):
                # Collect product urls
                articles = self.se.find_elements(
                    '.displayProducts__chooseView article')
                product_urls += [self.se.find_element('a', parent=article).get_attribute(  # type: ignore
                    'href') for article in articles]  

                # Next page
                ul = self.se.find_element('ul.pagination')
                assert ul is not None, 'Pagination not found'
                lis = self.se.find_elements('li', parent=ul)
                lis[-1].click()
                self.se.wait_random_time(3, 4)
        except AssertionError as e:
            print(str(e))

        return product_urls

    def check_unlisted_products(self, products, catalogs) -> None:
        print('Finding Inventory Unlisted Products:')
        print('Inventory size: {} Products'.format(len(products)))

        EANs = [product['EAN'] for product in products]
        URLs = [product['url'] for product in products]
        err_count = 0

        if self.test_env:
            print('Test mode: Only 3 catalogs and 10 product per catalog.')
            catalogs = catalogs[:3]

        print('Total Catalogs: {}'.format(len(catalogs)))
        for i, catalog in enumerate(catalogs):
            unlisted = []

            product_urls = list(
                set(self.product_urls_by_catalogue(catalog['url'])))
            in_inventory = [url for url in product_urls if url in URLs]
            product_urls_filtered = [
                url for url in product_urls if url not in URLs]

            if self.test_env:
                product_urls_filtered = product_urls_filtered[:10]
            print('{}. Catalogue: {}, Items: {}, In Inventory: {}, To Collect: {}, '.format(
                i+1, catalog['name'], len(product_urls), len(in_inventory), len(product_urls_filtered)), end='')

            with ThreadPoolExecutor(max_workers=15) as executor:
                for new_product, err in executor.map(self.scrape_product, product_urls_filtered, [True] * len(product_urls_filtered)):
                    if err:
                        err_count += 1
                        continue
                    elif new_product['EAN'] not in EANs:
                        unlisted.append({
                            'EAN': new_product['EAN'],
                            'product_name': new_product['product_name'],
                            'quantity': new_product['quantity'],
                        })

            # Write to csv
            sub_directory = os.path.join(
                self.file_directory, 'category_of_interest')
            if not os.path.exists(sub_directory):
                os.mkdir(sub_directory)
            filename = os.path.join(sub_directory, '{}'.format(
                catalog.get('filename', 'Unnamed')))
            fs.write_to_sheet(pd.DataFrame(unlisted), filename)
            print('Collected: {}'.format(len(unlisted)))

        print('Error count: {}'.format(err_count))
