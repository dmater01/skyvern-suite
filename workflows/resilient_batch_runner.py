import asyncio
from skyvern import Skyvern

async def recover_page_state(page):
    try:
        await page.act("Close or dismiss any active modal, popup, cookie banner, or alert overlay.")
        await asyncio.sleep(1)
    except Exception as e:
        print(f"Recovery failed: {e}")

async def ensure_authenticated(page, credential_id="cred_id"):
    is_logged_in = await page.validate("Check if the user is currently logged into the account dashboard")
    if not is_logged_in:
        await page.agent.login(credential_type="bitwarden", credential_id=credential_id)

async def main():
    skyvern = Skyvern.local()
    browser = await skyvern.launch_cloud_browser()
    page = await browser.get_working_page()
    await page.goto("https://portal.example.com")
    await browser.close()

if __name__ == "__main__":
    asyncio.run(main())