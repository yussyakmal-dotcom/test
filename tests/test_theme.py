"""Browser regression tests against a running application (no Odoo credentials needed).
Run: python tests/test_theme.py --url http://localhost
"""
import argparse
import os
from playwright.sync_api import sync_playwright


def main(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or None, args=['--no-sandbox'])
        context = browser.new_context(color_scheme='light')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/login')
        page.goto(url + '/appearance')
        assert page.url == url + '/login'
        picker = page.locator('[data-theme-picker]')
        assert page.locator('html').get_attribute('data-theme') == 'light'
        for theme in ('dark', 'light', 'ocean'):
            picker.select_option(theme)
            assert page.locator('html').get_attribute('data-theme') == theme
            expected = {'dark': 'rgb(20, 22, 27)', 'light': 'rgb(245, 246, 248)', 'ocean': 'rgb(234, 244, 248)'}[theme]
            assert page.locator('body').evaluate('(el) => getComputedStyle(el).backgroundColor') == expected
            page.reload()
            assert picker.input_value() == theme
            assert page.locator('html').get_attribute('data-theme') == theme
        other = context.new_page()
        other.goto(url + '/login')
        picker.select_option('dark')
        other.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        picker.select_option('system')
        page.emulate_media(color_scheme='dark')
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        page.emulate_media(color_scheme='light')
        page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
        page.evaluate("localStorage.setItem('odoobi.desktop.theme', 'invalid')")
        page.reload()
        assert picker.input_value() == 'system'
        assert page.locator('html').get_attribute('data-theme') == 'light'
        username = os.getenv('ODOOBI_TEST_USERNAME')
        password = os.getenv('ODOOBI_TEST_PASSWORD')
        if username and password:
            page.locator('[name="username"]').fill(username)
            page.locator('[name="password"]').fill(password)
            page.locator('form button').click()
            page.wait_for_url(url + '/')
            for path in ('/appearance', '/ro', '/ro/master/template', '/ro/master/jadwal'):
                response = page.goto(url + path)
                assert response.status == 200, path
                picker.select_option('ocean')
                assert page.locator('html').get_attribute('data-theme') == 'ocean'
                assert page.locator('html').get_attribute('data-bs-theme') == 'light'
                picker.select_option('dark')
                assert page.locator('html').get_attribute('data-bs-theme') == 'dark'
                page.reload()
                assert picker.input_value() == 'dark', path
            print('PASS: authenticated theme page, RO form, Master Template and Master Jadwal')
        blocked = browser.new_context()
        blocked.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('Blocked'); } });")
        blocked_page = blocked.new_page()
        blocked_page.goto(url + '/login')
        blocked_page.locator('[data-theme-picker]').select_option('dark')
        assert blocked_page.locator('html').get_attribute('data-theme') == 'dark'
        assert not errors, errors
        browser.close()
    print('PASS: theme choices, reload persistence, cross-tab sync, system changes, invalid preference, CSP execution')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost')
    main(parser.parse_args().url.rstrip('/'))
