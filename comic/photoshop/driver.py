"""Driving Photoshop: COM to reach it, ExtendScript (JSX) to do the work.

Photoshop's COM object model can do most things, but each call is a
cross-process round trip and text layers need dozens of them. So the COM side
does exactly one thing per page -- ``DoJavaScript`` -- and the page is built
inside Photoshop by one script that receives its plan as a JS object literal
(JSON is valid JS; ExtendScript has no ``JSON`` object of its own).

Photoshop is a narrow tool here like every other model in the project: it
fills and sets type exactly as the plan says. It decides nothing.

**Content-Aware Fill only, never Generative Fill.** Content-Aware Fill runs on
this machine. Generative Fill sends the image to Adobe's servers, which is a
network call to a model API -- out of scope for this project.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from comic.photoshop.plan import PagePlan

PROG_ID = "Photoshop.Application"


class PhotoshopError(RuntimeError):
    pass


def registered_executable() -> str | None:
    """Where COM will launch Photoshop from, or None if it is not registered."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, PROG_ID + r"\CLSID") as key:
            clsid = winreg.QueryValue(key, None)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
            return winreg.QueryValue(key, None)
    except OSError:
        return None


def connect():
    """Attach to Photoshop, launching it if needed (about 15 s cold)."""
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:  # pragma: no cover - environment
        raise PhotoshopError("pywin32 is not installed: python -m pip install pywin32") from exc
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch(PROG_ID)
    except Exception as exc:  # noqa: BLE001 - COM raises its own types
        raise PhotoshopError(f"could not start Photoshop over COM: {exc}") from exc


def run(app, script: str) -> str:
    """Run a JSX script; a result starting ``ERR:`` becomes an exception."""
    result = str(app.DoJavaScript(script))
    if result.startswith("ERR:"):
        raise PhotoshopError(result[4:].strip())
    return result


def missing_fonts(app, postscript_names: set[str]) -> list[str]:
    """Which of these faces Photoshop cannot see (it reads fonts at startup)."""
    names = json.dumps(sorted(postscript_names))
    script = (
        "var want=" + names + ",miss=[];"
        "for(var i=0;i<want.length;i++){try{app.fonts.getByName(want[i]);}"
        "catch(e){miss.push(want[i]);}}miss.join('|');"
    )
    out = run(app, script)
    return [n for n in out.split("|") if n]


_PAGE_JSX = r"""
function cTID(s){return charIDToTypeID(s);} function sTID(s){return stringIDToTypeID(s);}
function rgb(c){var x=new SolidColor();x.rgb.red=c[0];x.rgb.green=c[1];x.rgb.blue=c[2];return x;}
function px(v){return new UnitValue(v,'px');}

function openAt72(path){
  var d=app.open(new File(path));
  if(d.mode!=DocumentMode.RGB) d.changeMode(ChangeMode.RGB);
  d.resizeImage(undefined,undefined,72,ResampleMethod.NONE);
  return d;
}

function strokeFx(size, c){
  var d=new ActionDescriptor(), ref=new ActionReference();
  ref.putProperty(cTID('Prpr'),cTID('Lefx'));
  ref.putEnumerated(cTID('Lyr '),cTID('Ordn'),cTID('Trgt'));
  d.putReference(cTID('null'),ref);
  var fx=new ActionDescriptor(); fx.putUnitDouble(cTID('Scl '),cTID('#Prc'),100);
  var st=new ActionDescriptor();
  st.putBoolean(cTID('enab'),true);
  st.putEnumerated(cTID('Styl'),cTID('FStl'),cTID('OutF'));
  st.putEnumerated(cTID('PntT'),cTID('FrFl'),cTID('SClr'));
  st.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
  st.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
  st.putUnitDouble(cTID('Sz  '),cTID('#Pxl'),size);
  var col=new ActionDescriptor();
  col.putDouble(cTID('Rd  '),c[0]); col.putDouble(cTID('Grn '),c[1]); col.putDouble(cTID('Bl  '),c[2]);
  st.putObject(cTID('Clr '),cTID('RGBC'),col);
  fx.putObject(cTID('FrFX'),cTID('FrFX'),st);
  d.putObject(cTID('T   '),cTID('Lefx'),fx);
  executeAction(cTID('setd'),d,DialogModes.NO);
}

function contentAwareFill(doc, layer, maskPath){
  var m=openAt72(maskPath);
  m.selection.selectAll(); m.selection.copy();
  m.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument=doc;
  var ch=doc.channels.add(); ch.name='Cleaning mask';
  doc.activeChannels=[ch]; doc.paste(); doc.selection.deselect();
  doc.activeChannels=doc.componentChannels;
  doc.activeLayer=layer;
  doc.selection.load(ch, SelectionType.REPLACE);
  doc.selection.expand(px(2));
  var d=new ActionDescriptor();
  d.putEnumerated(cTID('Usng'),cTID('FlCn'),sTID('contentAware'));
  d.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
  d.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
  executeAction(cTID('Fl  '),d,DialogModes.NO);
  doc.selection.deselect();
}

function addSfx(doc, group, s){
  var lay=group.artLayers.add();
  lay.kind=LayerKind.TEXT;
  lay.name='SFX '+s.region_id+'  '+s.text;
  var ti=lay.textItem;
  ti.kind=TextType.POINTTEXT;
  ti.font=s.font;
  ti.size=px(100);
  ti.justification=Justification.CENTER;
  ti.antiAliasMethod=AntiAlias.SMOOTH;
  ti.color=rgb(s.colour);
  ti.contents=s.text.split(String.fromCharCode(10)).join(String.fromCharCode(13));
  ti.position=[px(s.cx),px(s.cy)];
  // Fit the level text to the original's footprint, then tilt it to match.
  var b=lay.bounds;
  var w=b[2].as('px')-b[0].as('px'), h=b[3].as('px')-b[1].as('px');
  // Fit the width, and the height of the bare letters: measure the word
  // without its diacritics, so the accents stand above the original's
  // capital height the way they do over any capital.
  if(s.plain && s.plain!=s.text){
    ti.contents=s.plain.split(String.fromCharCode(10)).join(String.fromCharCode(13));
    var pb=lay.bounds; h=pb[3].as('px')-pb[1].as('px');
    ti.contents=s.text.split(String.fromCharCode(10)).join(String.fromCharCode(13));
  }
  var k=Math.min(s.width/w, 1.1*s.height/h);
  // One line: the bare letters no taller than the original's letters.
  if(s.letter_height>0 && s.text.indexOf(String.fromCharCode(10))<0) k=Math.min(k, 1.08*s.letter_height/h);
  k*=100;
  lay.resize(k,k,AnchorPosition.MIDDLECENTER);
  if(s.angle) lay.rotate(s.angle, AnchorPosition.MIDDLECENTER);
  b=lay.bounds;
  lay.translate(px(s.cx-(b[0].as('px')+b[2].as('px'))/2), px(s.cy-(b[1].as('px')+b[3].as('px'))/2));
  if(s.stroke_ratio>0){doc.activeLayer=lay; strokeFx(Math.max(1,Math.round(s.height*s.stroke_ratio)), s.stroke_colour);}
}

function comicBuildPage(P){
  app.displayDialogs=DialogModes.NO;
  app.preferences.rulerUnits=Units.PIXELS;
  app.preferences.typeUnits=TypeUnits.PIXELS;

  var doc=openAt72(P.cleaned);
  var base=doc.activeLayer;
  if(base.isBackgroundLayer) base.isBackgroundLayer=false;
  base.name='Cleaned';

  // Photoshop hands back the already-open document when a file is opened
  // twice, so closing a second "original" would close this page with it.
  if(P.original==P.cleaned){
    base.duplicate(doc, ElementPlacement.PLACEATEND);
  } else {
    var o=openAt72(P.original);
    o.activeLayer.duplicate(doc, ElementPlacement.PLACEATEND);
    o.close(SaveOptions.DONOTSAVECHANGES);
  }
  app.activeDocument=doc;
  var orig=doc.layers[doc.layers.length-1];
  orig.name='Original'; orig.visible=false;

  if(P.fill_mask) contentAwareFill(doc, base, P.fill_mask);

  if(P.sfx.length){
    var sg=doc.layerSets.add(); sg.name='SFX';
    for(var j=0;j<P.sfx.length;j++) addSfx(doc, sg, P.sfx[j]);
  }

  var group=doc.layerSets.add(); group.name='Vietnamese text';
  for(var i=P.layers.length-1;i>=0;i--){
    var t=P.layers[i];
    var lay=group.artLayers.add();
    lay.kind=LayerKind.TEXT;
    lay.name=t.name;
    var ti=lay.textItem;
    ti.kind=TextType.POINTTEXT;
    ti.font=t.font;
    ti.size=px(t.size);
    ti.useAutoLeading=false;
    ti.leading=px(t.leading);
    ti.justification=Justification.CENTER;
    ti.antiAliasMethod=AntiAlias.SMOOTH;
    ti.color=rgb(t.colour);
    ti.contents=t.lines.join('\r');
    ti.position=[px(t.x),px(t.baseline)];
    if(t.stroke_px>0){doc.activeLayer=lay; strokeFx(t.stroke_px,t.stroke_colour);}
  }

  var psd=new PhotoshopSaveOptions(); psd.layers=true; psd.embedColorProfile=true;
  doc.saveAs(new File(P.psd), psd, true);
  doc.saveAs(new File(P.png), new PNGSaveOptions(), true, Extension.LOWERCASE);
  // The art alone -- dialogue, translated SFX and watermarks erased, nothing
  // set -- for `comic video`, which must never show translated lettering.
  group.visible=false; if(P.sfx.length) sg.visible=false;
  doc.saveAs(new File(P.png.replace(/\.png$/i,'_art.png')), new PNGSaveOptions(), true, Extension.LOWERCASE);
  doc.close(SaveOptions.DONOTSAVECHANGES);
  return 'ok '+P.layers.length+' '+P.sfx.length;
}

try { comicBuildPage(PLAN); } catch(e) { 'ERR: '+e+' (line '+e.line+')'; }
"""


def build_page(app, plan: PagePlan) -> float:
    """Build one page's PSD and PNG. Returns seconds taken."""
    started = time.time()
    script = "var PLAN = " + json.dumps(plan.to_script(), ensure_ascii=False) + ";\n" + _PAGE_JSX
    run(app, script)
    for out in (plan.psd, plan.png, art_path(plan.png)):
        if not Path(out).exists():
            raise PhotoshopError(f"Photoshop reported success but {out} was not written")
    return time.time() - started


def art_path(png: str | Path) -> Path:
    """Where a page's text-free export goes: ``012.png`` -> ``012_art.png``."""
    png = Path(png)
    return png.with_name(png.stem + "_art.png")


_ART_JSX = r"""
function comicExportArt(P){
  app.displayDialogs=DialogModes.NO;
  var doc=app.open(new File(P.psd));
  for(var i=0;i<doc.layerSets.length;i++){
    var n=doc.layerSets[i].name;
    if(n=='Vietnamese text' || n=='SFX') doc.layerSets[i].visible=false;
  }
  doc.saveAs(new File(P.art), new PNGSaveOptions(), true, Extension.LOWERCASE);
  doc.close(SaveOptions.DONOTSAVECHANGES);
  return 'ok';
}
try { comicExportArt(ART); } catch(e) { 'ERR: '+e+' (line '+e.line+')'; }
"""


def export_art(app, psd: Path) -> Path:
    """Save an existing PSD with its text groups hidden, as ``NNN_art.png``.

    For chapters built before the page script saved the art itself.
    """
    out = art_path(psd.with_suffix(".png"))
    script = "var ART = " + json.dumps({"psd": str(psd), "art": str(out)}) + ";\n" + _ART_JSX
    run(app, script)
    if not out.exists():
        raise PhotoshopError(f"Photoshop reported success but {out} was not written")
    return out
