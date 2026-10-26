"""Round46 real Music document: artwork drag, reorder, imports and native theme metadata."""
from tests.round44_music_test import music_browser, module_page

def test_cover_cannot_start_native_uri_drag_or_show_link_import(module_page):
    page,frame=module_page
    assert frame.locator("#drop").is_hidden()
    assert frame.locator("#np-img").evaluate("e=>getComputedStyle(e).webkitUserDrag")=="none"
    result=frame.evaluate("""() => {
      const image=document.querySelector('#np-img'),dt=new DataTransfer();
      dt.setData('text/uri-list',image.src);
      const event=new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt});
      return {accepted:image.dispatchEvent(event),prevented:event.defaultPrevented,drop:document.querySelector('#drop').hidden};
    }""")
    assert result=={"accepted":False,"prevented":True,"drop":True}
    box=frame.locator("#np-img").bounding_box()
    page.mouse.move(box["x"]+box["width"]*.5,box["y"]+box["height"]*.5)
    page.mouse.down();page.mouse.move(box["x"]+box["width"]*.5+70,box["y"]+box["height"]*.5+50,steps=8);page.mouse.up()
    assert frame.locator("#drop").is_hidden()
    assert frame.evaluate("MemuMusic.S.view")!="download"

def test_queue_reorder_from_artwork_stays_enabled_and_never_becomes_external_import(module_page):
    page,frame=module_page
    frame.locator('.mm-np-tabs [data-tab="queue"]').click()
    result=frame.evaluate("""() => {
      const rows=[...document.querySelectorAll('#np-queue .mm-qrow[draggable=true]')];
      const from=rows[1],to=rows[0],dt=new DataTransfer();
      const order=()=>MemuMusic.Player.st.order.map(i=>MemuMusic.Player.st.queue[i].id);
      const original=order();
      const event=new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt});
      const accepted=from.querySelector('img').dispatchEvent(event);
      document.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));
      const hidden=document.querySelector('#drop').hidden;
      const r=to.getBoundingClientRect();
      to.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientY:r.bottom-1}));
      document.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));
      return {accepted,types:[...dt.types],hidden,original,after:order()};
    }""")
    assert result["accepted"]
    assert "application/x-memu-music" in result["types"]
    assert result["hidden"]
    assert result["after"]==[result["original"][0],result["original"][2],result["original"][1]]

def test_external_music_link_still_opens_import(module_page):
    _,frame=module_page
    frame.evaluate("""() => {
      const dt=new DataTransfer();dt.setData('text/uri-list','https://www.youtube.com/watch?v=testmusic');
      document.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));
    }""")
    assert frame.locator("#drop").is_visible()
    frame.evaluate("""() => {
      const dt=new DataTransfer();dt.setData('text/uri-list','https://www.youtube.com/watch?v=testmusic');
      document.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
    }""")
    assert frame.locator("#drop").is_hidden()
    assert frame.evaluate("MemuMusic.S.view")=="download"

def test_real_queue_artwork_drag_keeps_native_reorder(module_page):
    page,frame=module_page
    frame.locator('.mm-np-tabs [data-tab="queue"]').click()
    frame.evaluate("""() => {window.__nativeDrag=[];document.addEventListener('dragstart',e=>__nativeDrag.push({prevented:e.defaultPrevented,types:[...e.dataTransfer.types]}));}""")
    rows=frame.locator('#np-queue .mm-qrow[draggable="true"]')
    source=rows.nth(1).locator("img").bounding_box();target=rows.nth(0).bounding_box()
    page.mouse.move(source["x"]+source["width"]*.5,source["y"]+source["height"]*.5)
    page.mouse.down();page.mouse.move(target["x"]+100,target["y"]+target["height"]*.5,steps=12);page.mouse.up()
    calls=frame.evaluate("__nativeDrag")
    assert any(not row["prevented"] and "application/x-memu-music" in row["types"] for row in calls),calls
    assert frame.locator("#drop").is_hidden()

def test_external_audio_file_still_uses_file_import_without_link_navigation(module_page):
    page,frame=module_page
    frame.evaluate("""() => {
      window.__uploads=[];const original=MemuMusic.API.call;
      MemuMusic.API.call=async(name,args)=>{if(name==='library.upload'){__uploads.push(args.name);return {ok:true};}return original(name,args);};
      const dt=new DataTransfer();dt.items.add(new File(['offline audio'],'regression.mp3',{type:'audio/mpeg'}));
      dt.setData('text/uri-list','file:///regression.mp3');
      document.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));
      window.__dropFiles=document.querySelector('#drop').classList.contains('files');
      document.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
    }""")
    for _ in range(30):
        if frame.evaluate("__uploads.length"):break
        page.wait_for_timeout(50)
    assert frame.evaluate("__dropFiles")
    assert frame.evaluate("__uploads")==["regression.mp3"]
    assert frame.evaluate("MemuMusic.S.view")!="download"
    assert frame.locator("#drop").is_hidden()

def test_music_and_player_send_their_actual_background_to_detached_native_host(module_page):
    _,frame=module_page
    values=frame.evaluate("""() => {
      window.__themes=[];
      Memu.appbar.set=async value=>{__themes.push(value);return {ok:true};};
      // A system caption still needs native appearance updates.
      Memu.appbar.state=()=>({active:false});
      MemuMusic.NP().close();
      MemuMusic.Theme.set('mode','light');MemuMusic.Theme.set('npTheme','dark');
      MemuMusic.NP().open();MemuMusic.Theme.appbar();
      const player=__themes.at(-1);
      MemuMusic.NP().close();MemuMusic.Theme.appbar();
      return {player,library:__themes.at(-1),actual: getComputedStyle(document.body).backgroundColor};
    }""")
    assert values["player"]["dark"] is True
    assert values["player"]["background"]=="#050507"
    assert values["library"]["dark"] is False
    assert values["library"]["background"]!="#050507"
    assert values["library"]["background"].startswith("#")
