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

function rgbDesc(c){
  var col=new ActionDescriptor();
  col.putDouble(cTID('Rd  '),c[0]); col.putDouble(cTID('Grn '),c[1]); col.putDouble(cTID('Bl  '),c[2]);
  return col;
}

// An SFX's effects, as the original has them (the operator, 2026-11-09):
// stroke, a second stroke (an outer glow at full spread draws a hard ring
// past the stroke), a soft glow, a drop shadow.
function sfxFx(s, strokePx){
  var d=new ActionDescriptor(), ref=new ActionReference();
  ref.putProperty(cTID('Prpr'),cTID('Lefx'));
  ref.putEnumerated(cTID('Lyr '),cTID('Ordn'),cTID('Trgt'));
  d.putReference(cTID('null'),ref);
  var fx=new ActionDescriptor(); fx.putUnitDouble(cTID('Scl '),cTID('#Prc'),100);
  var any=false;
  if(strokePx>0){
    var st=new ActionDescriptor();
    st.putBoolean(cTID('enab'),true);
    st.putEnumerated(cTID('Styl'),cTID('FStl'),cTID('OutF'));
    st.putEnumerated(cTID('PntT'),cTID('FrFl'),cTID('SClr'));
    st.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
    st.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
    st.putUnitDouble(cTID('Sz  '),cTID('#Pxl'),strokePx);
    st.putObject(cTID('Clr '),cTID('RGBC'),rgbDesc(s.stroke_colour));
    fx.putObject(cTID('FrFX'),cTID('FrFX'),st); any=true;
  }
  var ring=s.stroke2_px>0, glow=s.glow_px>0;
  if(ring || glow){
    var g=new ActionDescriptor();
    g.putBoolean(cTID('enab'),true);
    g.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
    g.putObject(cTID('Clr '),cTID('RGBC'),rgbDesc(ring ? s.stroke2_colour : s.glow_colour));
    g.putUnitDouble(cTID('Opct'),cTID('#Prc'),ring ? 100 : 85);
    g.putEnumerated(cTID('GlwT'),cTID('BETE'),ring ? cTID('PrBL') : cTID('SfBL'));
    g.putUnitDouble(cTID('Ckmt'),cTID('#Prc'),ring ? 100 : 10);
    g.putUnitDouble(cTID('blur'),cTID('#Pxl'),(ring ? s.stroke2_px : s.glow_px)+strokePx);
    g.putUnitDouble(cTID('Nose'),cTID('#Prc'),0);
    g.putUnitDouble(cTID('Inpr'),cTID('#Prc'),50);
    fx.putObject(cTID('OrGl'),cTID('OrGl'),g); any=true;
  }
  if(s.shadow_dx || s.shadow_dy){
    var sh=new ActionDescriptor();
    sh.putBoolean(cTID('enab'),true);
    sh.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
    sh.putObject(cTID('Clr '),cTID('RGBC'),rgbDesc(s.shadow_colour));
    sh.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
    sh.putBoolean(cTID('uglg'),false);
    // Photoshop's angle is where the light comes from; the shadow falls opposite.
    sh.putUnitDouble(cTID('lagl'),cTID('#Ang'),Math.atan2(s.shadow_dy,-s.shadow_dx)*180/Math.PI);
    sh.putUnitDouble(cTID('Dstn'),cTID('#Pxl'),Math.sqrt(s.shadow_dx*s.shadow_dx+s.shadow_dy*s.shadow_dy));
    sh.putUnitDouble(cTID('Ckmt'),cTID('#Prc'),100);
    sh.putUnitDouble(cTID('blur'),cTID('#Pxl'),Math.max(1,strokePx));
    fx.putObject(cTID('DrSh'),cTID('DrSh'),sh); any=true;
  }
  if(s.gradient_top && s.gradient_bottom){
    // Gradient overlay, bottom colour to top colour at 90 degrees.
    var g2=new ActionDescriptor();
    g2.putBoolean(cTID('enab'),true);
    g2.putEnumerated(cTID('Md  '),cTID('BlnM'),cTID('Nrml'));
    g2.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
    var grad=new ActionDescriptor();
    grad.putString(cTID('Nm  '),'SFX');
    grad.putEnumerated(cTID('GrdF'),cTID('GrdF'),cTID('CstS'));
    grad.putDouble(cTID('Intr'),4096);
    var clrs=new ActionList(), trns=new ActionList(), stops=[[s.gradient_bottom,0],[s.gradient_top,4096]];
    for(var q=0;q<2;q++){
      var cs=new ActionDescriptor();
      cs.putObject(cTID('Clr '),cTID('RGBC'),rgbDesc(stops[q][0]));
      cs.putEnumerated(cTID('Type'),cTID('Clry'),cTID('UsrS'));
      cs.putInteger(cTID('Lctn'),stops[q][1]); cs.putInteger(cTID('Mdpn'),50);
      clrs.putObject(cTID('Clrt'),cs);
      var ts=new ActionDescriptor();
      ts.putUnitDouble(cTID('Opct'),cTID('#Prc'),100);
      ts.putInteger(cTID('Lctn'),stops[q][1]); ts.putInteger(cTID('Mdpn'),50);
      trns.putObject(cTID('TrnS'),ts);
    }
    grad.putList(cTID('Clrs'),clrs); grad.putList(cTID('Trns'),trns);
    g2.putObject(cTID('Grad'),cTID('Grdn'),grad);
    g2.putUnitDouble(cTID('Angl'),cTID('#Ang'),90);
    g2.putEnumerated(cTID('Type'),cTID('GrdT'),cTID('Lnr '));
    g2.putBoolean(cTID('Rvrs'),false); g2.putBoolean(cTID('Algn'),true);
    g2.putUnitDouble(cTID('Scl '),cTID('#Prc'),100);
    fx.putObject(cTID('GrFl'),cTID('GrFl'),g2); any=true;
  }
  if(!any) return;
  d.putObject(cTID('T   '),cTID('Lefx'),fx);
  executeAction(cTID('setd'),d,DialogModes.NO);
}

// A vertical SFX: one letter per line, each line moved along by tabs to give
// the column its lean (the operator's method, 2026-11-09). Returns the angle
// to turn the layer by when the lean is finer than one tab.
function verticalLines(lay, ti, s){
  var CR=String.fromCharCode(13), TAB=String.fromCharCode(9);
  ti.justification=Justification.LEFT;
  ti.useAutoLeading=false; ti.leading=px(100);
  ti.contents='X'; var b0=lay.bounds;
  ti.contents=TAB+'X'; var b1=lay.bounds;
  var tabw=(b1[2].as('px')-b1[0].as('px'))-(b0[2].as('px')-b0[0].as('px'));
  var lines=s.text.split(String.fromCharCode(10)), plain=(s.plain||s.text).split(String.fromCharCode(10));
  var n=lines.length, step=s.slant*100, lo=Math.min(0,(n-1)*step);
  var tabs=[], used=0;
  for(var i=0;i<n;i++){ var k=tabw>0?Math.round((i*step-lo)/tabw):0; tabs.push(k); used+=k; }
  function build(src){ var out=[]; for(var i=0;i<src.length;i++){ var t=''; for(var j=0;j<tabs[i];j++) t+=TAB; out.push(t+src[i]); } return out.join(CR); }
  s._plain=build(plain);
  ti.contents=build(lines);
  return (used==0 && Math.abs(s.slant)>0.05) ? -Math.atan(s.slant)*180/Math.PI : 0;
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
  var turn=s.angle;
  if(s.vertical){ turn=verticalLines(lay, ti, s); }
  // Fit the level text to the original's footprint, then tilt it to match.
  var b=lay.bounds;
  var w=b[2].as('px')-b[0].as('px'), h=b[3].as('px')-b[1].as('px');
  // Fit the width, and the height of the bare letters: measure the word
  // without its diacritics, so the accents stand above the original's
  // capital height the way they do over any capital.
  if(s.plain && s.plain!=s.text){
    var full=ti.contents;
    ti.contents=s.vertical ? s._plain : s.plain.split(String.fromCharCode(10)).join(String.fromCharCode(13));
    var pb=lay.bounds; h=pb[3].as('px')-pb[1].as('px');
    ti.contents=full;
  }
  // Never scale to nothing: a 0x0 footprint set the word at 0 px and hung the build.
  if(!(s.width>0 && s.height>0 && w>0 && h>0)){ lay.remove(); return; }
  var k=Math.min(s.width/w, 1.1*s.height/h);
  // One line: the bare letters no taller than the original's letters.
  if(s.letter_height>0 && s.text.indexOf(String.fromCharCode(10))<0) k=Math.min(k, 1.08*s.letter_height/h);
  k*=100;
  lay.resize(k,k,AnchorPosition.MIDDLECENTER);
  if(turn) lay.rotate(turn, AnchorPosition.MIDDLECENTER);
  b=lay.bounds;
  lay.translate(px(s.cx-(b[0].as('px')+b[2].as('px'))/2), px(s.cy-(b[1].as('px')+b[3].as('px'))/2));
  doc.activeLayer=lay;
  sfxFx(s, s.stroke_ratio>0 ? Math.max(1,Math.round(Math.min(s.width,s.height)*s.stroke_ratio)) : 0);
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
  // Original at the bottom and visible, Cleaned above it: lowering Cleaned's
  // opacity shows the original through, for checking (the operator, 2026-11-08).
  var orig=doc.layers[doc.layers.length-1];
  orig.name='Original'; orig.visible=true;

  if(P.fill_mask) contentAwareFill(doc, base, P.fill_mask);

  if(P.sfx.length){
    var sg=doc.layerSets.add(); sg.name='SFX';
    for(var j=0;j<P.sfx.length;j++) addSfx(doc, sg, P.sfx[j]);
  }

  var group=doc.layerSets.add(); group.name='Thoại';
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
    if(t.hscale && t.hscale!=100){ti.horizontalScale=t.hscale;}
    if(t.angle){lay.rotate(t.angle, AnchorPosition.MIDDLECENTER);}
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
    if(n=='Vietnamese text' || n=='Thoại' || n=='SFX') doc.layerSets[i].visible=false;
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


_EXPORT_JSX = r"""
function comicExportPng(E){
  app.displayDialogs=DialogModes.NO;
  var doc=app.open(new File(E.psd));
  doc.saveAs(new File(E.png), new PNGSaveOptions(), true, Extension.LOWERCASE);
  doc.close(SaveOptions.DONOTSAVECHANGES);
  return 'ok';
}
try { comicExportPng(EXPORT); } catch(e) { 'ERR: '+e+' (line '+e.line+')'; }
"""


def stale_exports(ps_dir: Path) -> list[Path]:
    """PSDs saved after their PNG: the operator edited them (2026-11-08)."""
    out = []
    for psd in sorted(ps_dir.glob("[0-9][0-9][0-9].psd")):
        png = psd.with_suffix(".png")
        if not png.exists() or psd.stat().st_mtime > png.stat().st_mtime + 1:
            out.append(psd)
    return out


def export_png(app, psd: Path) -> Path:
    """Save a PSD exactly as the operator left it (visible layers) as its PNG."""
    png = psd.with_suffix(".png")
    before = png.stat().st_mtime if png.exists() else 0.0
    script = ("var EXPORT = " + json.dumps({"psd": str(psd), "png": str(png)}) + ";\n"
              + _EXPORT_JSX)
    run(app, script)
    if not png.exists() or png.stat().st_mtime <= before:
        raise PhotoshopError(f"Photoshop reported success but {png} was not rewritten")
    return png
