"""Browser smoke test for UI PRs. Run while preview server is available on localhost:3000."""
import os
from playwright.sync_api import sync_playwright

base = os.environ.get('WARDROBE_BASE_URL','http://127.0.0.1:3000')
executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')

with sync_playwright() as p:
    options={'headless':True}
    if executable:options['executable_path']=executable
    options['args']=['--no-sandbox']
    browser=p.chromium.launch(**options)
    page=browser.new_page(viewport={'width':1440,'height':900})
    errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base,wait_until='domcontentloaded')
    # Intro opens automatically; the animation phase should finish promptly.
    entrance=page.locator('#fs-entrance')
    entrance.wait_for(state='visible',timeout=10000)
    assert entrance.locator('.fs-entrance__hint, .fs-entrance__trigger').count()==0
    animation_started=page.evaluate('performance.now()')
    entrance.wait_for(state='detached',timeout=5000)
    animation_elapsed=page.evaluate('performance.now()')-animation_started
    assert animation_elapsed < 2300, f'Wardrobe opening animation took too long: {animation_elapsed:.0f}ms'
    page.locator('[data-action="demoLogin"]').click()
    page.locator('.topbar').wait_for(timeout=20000)

    page.goto(base+'/wardrobe')
    page.locator('.clothing-card').first.wait_for(timeout=20000)
    cards=page.locator('.clothing-card')
    assert cards.count()>0, 'No wardrobe cards rendered'
    for card in cards.all():
        for action in ('addOwnToOOTD','editClothing','deleteClothing'):
            assert card.locator(f'[data-action="{action}"]').count()==1, f'Missing {action}'
    page.locator('[data-action="openAdd"]').click()
    page.locator('#clothing-form').wait_for()
    page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).click()

    page.goto(base+'/ootd')
    page.locator('.topbar').wait_for()
    # OOTD detail regression: clothing stickers must stay inside the preview,
    # not overlap the Outfit Pieces panel. This was missing outside gallery cards.
    if page.locator('.outfit-card [data-action="showOutfit"]').count():
        page.locator('.outfit-card [data-action="showOutfit"]').first.click()
        page.locator('.outfit-detail-preview .preview').wait_for()
        assert page.evaluate('''() => {
            const stage = document.querySelector('.outfit-detail-preview .preview');
            const pieces = document.querySelector('.outfit-detail-pieces');
            const s = stage.getBoundingClientRect();
            const p = pieces.getBoundingClientRect();
            const css = getComputedStyle(stage);
            return css.position === 'relative' && css.overflow === 'hidden'
                && (s.right <= p.left + 1 || s.bottom <= p.top + 1);
        }'''), 'OOTD modal preview overlaps the outfit details or lacks clipping'
        page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).click()
    page.locator('[data-action="newOOTD"]').first.click()
    page.locator('#editor-canvas').wait_for(timeout=20000)
    page.goto(base+'/friends')
    page.locator('.topbar').wait_for()
    page.goto(base+'/borrowing')
    page.locator('.topbar').wait_for()
    page.goto(base+'/notifications')
    page.locator('#reminder-form input[name="title"]').wait_for(timeout=20000)
    page.locator('[data-action="notifyActivity"]').click()
    page.locator('#journal-form').wait_for()
    page.locator('[data-action="notifyBorrowLogs"]').click()
    page.locator('#borrow-log-form').wait_for()
    page.goto(base+'/profile')
    page.locator('#profile-form input[name="email"]').wait_for()
    assert page.locator('[data-action="deleteAccount"]').count()==1
    assert not errors, 'JavaScript errors: '+str(errors)
    browser.close()
print('Browser UI smoke passed: cards, actions, forms, OOTD Studio, Friends and Borrowing')
