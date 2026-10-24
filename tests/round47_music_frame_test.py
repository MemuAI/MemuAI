"""Real Music iframe AND actual glass host: the missing layer in earlier tests."""
from __future__ import annotations

import io
import json
from pathlib import Path
import pytest
from tests.round44_music_test import music_browser, module_page

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/"app/pgstatic"


def host(page, theme, detached, full=True):
    from app.themes import theme_css
    page.add_style_tag(content=theme_css())
    # Same relevant sheets and order as the real module app document. Previous
    # tests checked only the iframe against a plain background, omitting this.
    for name in ("style.css","modules.css","module-apps.css","module-appbar.css","designs/glass.css"):
        page.add_style_tag(content=(STATIC/name).read_text("utf-8"))
    with page.expect_event("frameattached") as attached:
        page.evaluate("""({theme,detached,full})=>{
      const root=document.documentElement;root.dataset.theme=theme;root.dataset.design='glass';
      if(detached)root.dataset.moduleApp='music';else delete root.dataset.moduleApp;
      root.dataset.motion='off';
      const frame=document.getElementById('f'), win=document.createElement('div');
      win.className='module-window';win.dataset.module='music';win.dataset.full=full?'1':'0';
      win.style.cssText='position:fixed;left:0;top:0;width:100vw;height:100vh;max-width:none;max-height:none;display:flex;flex-direction:column;animation:none;--mx-x:0px;--mx-y:0px;--mx-w:100vw;--mx-h:100vh';
      const body=document.createElement('div');body.className='module-window-body';
      frame.before(win);win.append(body);body.append(frame);
    }""",{"theme":theme,"detached":detached,"full":full})
    page.add_script_tag(content=(STATIC/"module-appbar.js").read_text("utf-8"))
    page.evaluate("""()=>window.addEventListener('message',event=>{
      const data=event.data||{};
      if(data.type==='memu-module' && data.action==='appbar')MemuModuleAppbar.fromModule('music',data.payload);
    })""")
    # Moving an iframe reloads its browsing context. Re-enter the actual
    # player after that reload, rather than accepting a blank frame screenshot.
    from round37_music_harness import wait
    frame=attached.value
    wait(page,frame,"() => !!(window.MemuMusic && MemuMusic.S.loaded)")
    frame.evaluate("""()=>{const M=MemuMusic;M.Theme.set('motion','off');M.Theme.set('mode','dark');M.Theme.set('npTheme','dark');M.Theme.set('bg','cover');M.Player.playList(M.S.tracks.slice(0,3),M.S.tracks[0].id,'Offline test');M.NP().open();}""")
    assert frame.locator("#np").is_visible()


@pytest.mark.parametrize("theme,detached",[("light",True),("dark",True),("light",False),("dark",False)])
def test_player_has_no_specular_outer_frame_at_window_and_screen_sizes(module_page,theme,detached):
    from PIL import Image
    page,frame=module_page
    host(page,theme,detached)
    output=ROOT/"build/round47-ui";output.mkdir(parents=True,exist_ok=True)
    for width,height in ((1000,720),(1366,720)):
        page.set_viewport_size({"width":width,"height":height})
        page.wait_for_timeout(150)
        data=page.screenshot()
        image=Image.open(io.BytesIO(data)).convert("RGB")
        image.save(output/f"music-host-{theme}-{'detached' if detached else 'expanded'}-{width}.png")
        metrics=page.locator('.module-window').evaluate("e=>({rim:getComputedStyle(e,'::after').content,background:getComputedStyle(e,'::after').backgroundImage})")
        sx,sy=image.width/width,image.height/height
        pairs=[((0,100),(3,100)),((width-1,height-70),(width-4,height-70)),((100,0),(100,3)),((width-150,height-1),(width-150,height-4))]
        deltas=[]
        for edge,inside in pairs:
            a=image.getpixel((min(image.width-1,int(edge[0]*sx)),min(image.height-1,int(edge[1]*sy))))
            b=image.getpixel((min(image.width-1,int(inside[0]*sx)),min(image.height-1,int(inside[1]*sy))))
            deltas.append(max(abs(x-y) for x,y in zip(a,b)))
        assert metrics["rim"] in ("none","normal") and max(deltas)<15, json.dumps({**metrics,"edge_delta":deltas})
        if detached:
            assert page.evaluate("document.documentElement.style.getPropertyValue('--module-app-bg')")=='#050507'
            (output/f'music-physical-{theme}-{width}.json').write_text(json.dumps({
                'styles':page.evaluate("""()=>[document.documentElement,document.body,document.querySelector('.module-window'),document.querySelector('.module-window-body'),document.getElementById('f')].map(e=>({tag:e.tagName,rect:e.getBoundingClientRect().toJSON(),bg:getComputedStyle(e).background,filter:getComputedStyle(e).backdropFilter}))"""),
                'pixel':image.getpixel((image.width-1,125)),
                'dpr':page.evaluate('devicePixelRatio')}),encoding='utf-8')
            # Also inspect the final physical pixel, including fractional-DPI
            # coverage; CSS width-1 alone misses a partially covered column.
            for edge,inside in (((image.width-1,125),(image.width-4,125)),((125,image.height-1),(125,image.height-4))):
                a,b=image.getpixel(edge),image.getpixel(inside)
                assert max(a)-max(b)<35,(edge,a,b)


def test_floating_glass_module_keeps_its_intended_inner_rim(module_page):
    page,_=module_page
    host(page,"dark",False,full=False)
    assert page.locator('.module-window').evaluate("getComputedStyle(document.querySelector('.module-window'),'::after').content")=='""'
