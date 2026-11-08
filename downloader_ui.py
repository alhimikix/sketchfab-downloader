"""Native desktop front end for Sketchfab and Fab preview exports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
import urllib.error
import sys
from platform_support import settings_directory,default_download_directory,open_path
from platform_support import open_url
from app_version import VERSION
from i18n import CATALOGS, LANGUAGES, Message, get_language, set_language, render, t
from updater import ReleaseClient, UpdateError, RELEASE_PAGE, prepare_update, start_installer, cleanup_completed_updates


class LocalizedVar(tk.StringVar):
    def set(self, value):
        self.message = value
        super().set(value)

    def retranslate(self):
        if hasattr(self, 'message'):
            self.set(render(self.message))

from sketchfab_local import BUNDLE_ROOT, DEFAULT_URL, ROOT, model_source, run_export

BG = '#0c1422'
PANEL = '#131f30'
FIELD = '#0e1929'
LINE = '#28364a'
TEXT = '#edf3fa'
MUTED = '#91a2b8'
ACCENT = '#65dfb5'
ERROR = '#ff9c9c'


class Downloader(tk.Tk):
    def __init__(self, receipt=None, auto_updates=True, language=None, persist_settings=True):
        super().__init__()
        self.title('Sketchfab Local')
        if sys.platform=='win32' and (BUNDLE_ROOT/'app.ico').is_file():
            self.iconbitmap(default=str(BUNDLE_ROOT/'app.ico'))
        self.configure(bg=BG)
        self.geometry('800x700')
        self.minsize(720, 680)
        self.option_add('*Font', ('Segoe UI', 10))
        self.option_add('*selectBackground', '#285b65')
        self.option_add('*selectForeground', TEXT)
        self.events = queue.Queue()
        self.translated_widgets = []
        self.log_messages = []
        self.update_busy = False
        self.update_checking = False
        self.update_window = None
        self.available_release = None
        self.running = False
        self.started = 0.0
        self.textures_done = 0
        self.result = None
        self.details_open = False
        self.settings_path = settings_directory() / '.ui-settings.json'
        self.persist_settings=persist_settings
        self.settings = self.read_settings() if persist_settings else {}
        set_language(language or self.settings.get('language',get_language()))
        self.url = tk.StringVar(value=self.settings.get('url', DEFAULT_URL))
        self.folder = tk.StringVar(value=self.settings.get('folder', str(default_download_directory())))
        self.status = LocalizedVar();self.status.set(t('ui.ready'))
        self.description = LocalizedVar();self.description.set(t('ui.ready_hint'))
        self.update_text = LocalizedVar();self.update_text.set(t('update.ready'))
        self.auto_updates = tk.BooleanVar(value=self.settings.get('auto_updates',True))
        self.elapsed = tk.StringVar(value='')
        self.build()
        if persist_settings:self.restore_result()
        self.bind('<Control-v>', self.on_paste_shortcut, add='+')
        if sys.platform=='darwin':self.bind('<Command-v>',self.on_paste_shortcut,add='+')
        self.bind('<Return>', lambda _event: self.start() if not self.running else None)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(120, self.poll)
        if receipt:
            from updater_helper import write_json
            self.update_idletasks()
            write_json(receipt,{'version':VERSION})
        if persist_settings:self.read_update_result()
        if auto_updates:self.after(800,self.startup_update_check)

    def read_settings(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding='utf-8'))
            result={k:v for k,v in data.items() if k in ('url','folder','last_result') and isinstance(v,str)}
            if data.get('language') in LANGUAGES:result['language']=data['language']
            if isinstance(data.get('auto_updates'),bool):result['auto_updates']=data['auto_updates']
            return result
        except (OSError, ValueError, AttributeError):
            return {}

    def save_settings(self):
        if not self.persist_settings:return
        data = {'url':self.url.get().strip(), 'folder':self.folder.get().strip(),
                'language':get_language(),'auto_updates':self.auto_updates.get()}
        if self.result:
            data['last_result'] = str(self.result)
        try:
            self.settings_path.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.settings_path)
        except OSError as exc:
            self.append_log(t('ui.settings_failed',error=exc))

    def label(self, parent, text=None, **options):
        widget=tk.Label(parent, text=text, bg=parent.cget('bg'), fg=TEXT,anchor='w', **options)
        if isinstance(text,Message):self.translated_widgets.append((widget,text))
        return widget

    def button(self, parent, text, command, primary=False, **options):
        widget=tk.Button(parent, text=text, command=command,
                         bg=ACCENT if primary else '#203047',
                         fg=BG if primary or sys.platform=='darwin' else TEXT,
                         activebackground='#8eebc9' if primary else '#2c405a',
                         activeforeground=BG if primary else TEXT,
                         disabledforeground='#77869b', relief='flat', bd=0,
                         highlightthickness=0, cursor='hand2', padx=16, pady=9,
                         font=('Segoe UI',11,'bold' if primary else 'normal'), **options)
        if isinstance(text,Message):self.translated_widgets.append((widget,text))
        return widget

    def entry(self, parent, variable):
        border = tk.Frame(parent,bg=LINE,padx=1,pady=1)
        inner = tk.Frame(border,bg=FIELD)
        inner.pack(fill='x')
        entry = tk.Entry(inner,textvariable=variable,bg=FIELD,fg=TEXT,
                         insertbackground=ACCENT,relief='flat',bd=0,
                         disabledbackground=FIELD,disabledforeground=MUTED,
                         font=('Segoe UI',11))
        entry.pack(fill='x',padx=12,pady=11)
        entry.bind('<FocusIn>',lambda _event:border.configure(bg=ACCENT))
        entry.bind('<FocusOut>',lambda _event:border.configure(bg=LINE))
        return border,entry

    def build(self):
        outer=tk.Frame(self,bg=BG,padx=28,pady=24)
        outer.pack(fill='both',expand=True)
        header=tk.Frame(outer,bg=BG);header.pack(fill='x',pady=(0,23))
        tk.Label(header,text='↓',font=('Segoe UI',26,'bold'),bg='#1b3839',fg=ACCENT,
                 width=2).pack(side='left',padx=(0,14))
        brand=tk.Frame(header,bg=BG);brand.pack(side='left')
        self.label(brand,'Sketchfab Local',font=('Segoe UI',23,'bold')).pack(anchor='w')
        self.label(brand,t('ui.subtitle'),font=('Segoe UI',11)).pack(anchor='w')
        tools=tk.Frame(header,bg=BG);tools.pack(side='right')
        self.language_names=[CATALOGS[code]['language.name'] for code in LANGUAGES]
        self.language_var=tk.StringVar(value=self.language_names[LANGUAGES.index(get_language())])
        self.language_selector=ttk.Combobox(tools,textvariable=self.language_var,values=self.language_names,
                                            state='readonly',width=12)
        self.language_selector.pack(anchor='e',pady=(0,6))
        self.language_selector.bind('<<ComboboxSelected>>',self.change_language)
        self.updates_button=self.button(tools,t('update.title'),self.show_updates)
        self.updates_button.configure(pady=4,font=('Segoe UI',10))
        self.updates_button.pack(anchor='e')
        self.label(brand,'v'+VERSION+' · BLEND + GLB',font=('Segoe UI',9)).pack(anchor='w',pady=(4,0))

        card=tk.Frame(outer,bg=PANEL,highlightbackground=LINE,highlightthickness=1,padx=22,pady=20)
        card.pack(fill='x')
        self.label(card,t('ui.model_url'),font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(0,8))
        row=tk.Frame(card,bg=PANEL);row.pack(fill='x')
        self.paste_button=self.button(row,t('ui.paste'),self.paste)
        self.paste_button.pack(side='right',padx=(10,0))
        field,self.url_entry=self.entry(row,self.url);field.pack(side='left',fill='x',expand=True)
        self.label(card,t('ui.folder'),font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(19,8))
        row=tk.Frame(card,bg=PANEL);row.pack(fill='x')
        self.browse_button=self.button(row,t('ui.browse'),self.browse)
        self.browse_button.pack(side='right',padx=(10,0))
        field,self.folder_entry=self.entry(row,self.folder);field.pack(side='left',fill='x',expand=True)
        self.download_button=self.button(card,t('ui.download'),self.start,primary=True)
        self.download_button.configure(font=('Segoe UI',12,'bold'),pady=12)
        self.download_button.pack(fill='x',pady=(22,10))
        hint=self.label(card,t('ui.blender_hint'),font=('Segoe UI',10))
        hint.configure(fg=MUTED);hint.pack(anchor='w')

        state=tk.Frame(outer,bg=PANEL,highlightbackground=LINE,highlightthickness=1,padx=20,pady=17)
        state.pack(fill='x',pady=(16,0))
        line=tk.Frame(state,bg=PANEL);line.pack(fill='x')
        self.dot=self.label(line,'●',font=('Segoe UI',12));self.dot.configure(fg=ACCENT)
        self.dot.pack(side='left',padx=(0,9))
        self.status_label=self.label(line,textvariable=self.status,font=('Segoe UI',12,'bold'))
        self.status_label.pack(side='left',fill='x',expand=True)
        self.label(line,textvariable=self.elapsed,font=('Segoe UI',10)).pack(side='right')
        self.detail_label=self.label(state,textvariable=self.description,font=('Segoe UI',10),justify='left',wraplength=650)
        self.detail_label.configure(fg=MUTED);self.detail_label.pack(fill='x',pady=(8,0))
        self.detail_label.bind('<Configure>',lambda event:self.detail_label.configure(wraplength=max(250,event.width-8)))
        style=ttk.Style(self);style.theme_use('clam')
        style.configure('Local.Horizontal.TProgressbar',troughcolor=PANEL,background=ACCENT,
                        bordercolor=PANEL,lightcolor=ACCENT,darkcolor=ACCENT)
        self.progress=ttk.Progressbar(state,mode='indeterminate',style='Local.Horizontal.TProgressbar')

        self.result_row=tk.Frame(outer,bg=BG)
        self.open_folder_button=self.button(self.result_row,t('ui.open_folder'),self.open_folder)
        self.open_folder_button.pack(side='left')
        self.open_model_button=self.button(self.result_row,t('ui.open_blend'),self.open_model)
        self.open_model_button.pack(side='left',padx=(10,0))
        self.result_size=self.label(self.result_row,'',font=('Segoe UI',10))
        self.result_size.configure(fg=MUTED);self.result_size.pack(side='right')

        self.more=self.button(outer,t('ui.details'),self.toggle_details)
        self.more.configure(bg=BG,fg=MUTED,activebackground=PANEL,anchor='w',padx=0,pady=6,font=('Segoe UI',10))
        self.more.pack(fill='x',pady=(10,0))
        self.log_box=tk.Frame(outer,bg=LINE,padx=1,pady=1)
        self.log=tk.Text(self.log_box,height=7,bg=FIELD,fg=MUTED,relief='flat',wrap='word',
                         font=('Consolas',9),padx=10,pady=8,state='disabled')
        scroll=ttk.Scrollbar(self.log_box,command=self.log.yview)
        scroll.pack(side='right',fill='y');self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left',fill='both',expand=True)
        self.update_notice=self.label(outer,'',font=('Segoe UI',9),wraplength=680)
        self.update_notice.configure(fg=ACCENT)
        self.update_notice.pack(fill='x',pady=(5,0))

    def restore_result(self):
        candidate=Path(self.settings['last_result']) if self.settings.get('last_result') else None
        if candidate is None:
            try:
                reports=list(Path(self.folder.get()).glob('*/report.json'))
                candidate=max(reports,key=lambda path:path.stat().st_mtime).parent if reports else None
            except OSError:
                candidate=None
        if candidate and (candidate/'model.glb').is_file():
            self.result=candidate
            self.show_result(previous=True)

    def on_paste_shortcut(self, _event):
        # Entries retain their normal Ctrl+V behavior. Elsewhere paste into URL.
        if not isinstance(self.focus_get(),(tk.Entry,tk.Text)):
            self.paste()
            return 'break'

    def paste(self):
        if self.running:return
        try:value=self.clipboard_get().strip()
        except tk.TclError:
            self.set_error(t('ui.empty_clipboard'))
            return
        self.url.set(value)
        self.url_entry.focus_set()
        self.url_entry.xview_moveto(0)
        self.status.set(t('ui.pasted'))
        self.description.set(t('ui.can_download'))
        self.dot.configure(fg=ACCENT)

    def browse(self):
        path=filedialog.askdirectory(parent=self,title=t('ui.choose_folder'),
                                     initialdir=self.folder.get() or str(ROOT),mustexist=False)
        if path:
            self.folder.set(path)
            self.save_settings()

    def set_error(self,text):
        self.status.set(t('ui.failed'))
        self.description.set(text)
        self.dot.configure(fg=ERROR)

    def set_busy(self,value):
        self.running=value
        for widget in (self.url_entry,self.folder_entry,self.paste_button,self.browse_button,self.download_button):
            widget.configure(state='disabled' if value else 'normal')
        self.download_button.configure(text=t('ui.downloading') if value else t('ui.download'))
        self.refresh_controls()
        if value:
            self.progress.pack(fill='x',pady=(13,0))
            self.progress.start(15)
        else:
            self.progress.stop();self.progress.pack_forget()

    def start(self):
        if self.running or self.update_busy:return
        try:
            model_source(self.url.get())
            if not self.folder.get().strip():raise ValueError(t('ui.choose_folder_error'))
        except ValueError as exc:
            self.set_error(str(exc));return
        self.save_settings()
        self.result_row.pack_forget()
        self.started=time.monotonic();self.textures_done=0
        self.log.configure(state='normal');self.log.delete('1.0','end');self.log.configure(state='disabled')
        self.log_messages.clear()
        self.status.set(t('ui.connecting'))
        self.description.set(t('ui.metadata'))
        self.dot.configure(fg=ACCENT)
        self.set_busy(True)
        url,folder=self.url.get().strip(),self.folder.get().strip()
        threading.Thread(target=self.work,args=(url,folder),daemon=True).start()

    def work(self,url,folder):
        try:
            result=run_export(url,folder,lambda line:self.events.put(('log',line)))
            self.events.put(('done',result))
        except Exception as exc:
            if isinstance(exc,urllib.error.HTTPError):
                message=t('ui.http_error',code=exc.code)
            elif isinstance(exc,urllib.error.URLError):
                message=t('ui.network_error')
            elif isinstance(exc,PermissionError):
                message=t('ui.permission_error')
            else:message=exc.args[0] if exc.args and isinstance(exc.args[0],Message) else str(exc)
            self.events.put(('error',(message,repr(exc))))

    def append_log(self,line):
        self.log_messages.append(line)
        self.log.configure(state='normal');self.log.insert('end',str(line)+'\n');self.log.see('end');self.log.configure(state='disabled')

    def update_stage(self,line):
        key=getattr(line,'key',None)
        if key=='export.viewer_opened':
            self.status.set(t('ui.loading'))
            self.description.set(t('ui.geometry_wait'))
        elif key=='export.geometry':
            self.status.set(t('ui.textures'))
            self.description.set(line)
        elif key=='material.texture':
            self.textures_done+=1
            self.status.set(t('ui.textures'))
            self.description.set(t('ui.texture_count',count=self.textures_done,texture=line))
        elif key=='export.writing':
            self.status.set(t('ui.assembling'))
            self.description.set(t('ui.writing'))
        elif key in ('export.loading_animation','export.animations','fab.baking'):
            self.status.set(t('ui.animations'))
            self.description.set(line)
        elif key in ('export.blender','blender.creating'):
            self.status.set(t('ui.blender'))
            self.description.set(t('ui.blender_work'))

    def show_result(self,previous=False):
        try:
            report=json.loads((self.result/'report.json').read_text(encoding='utf-8'))
            name=report.get('model',self.result.name)
            count=f'{report.get("triangles",0):,}'.replace(',',' ')
            size=(self.result/'model.glb').stat().st_size/1024/1024
            clip_count=len(report.get('animations',[]))
            suffix=t('ui.animation_count',count=clip_count) if clip_count else ''
            self.description.set(t('ui.result',name=name,count=count,suffix=suffix))
            self.open_model_button.configure(text=t('ui.open_blend') if (self.result/'model.blend').is_file() else t('ui.open_glb'))
            self.result_size.configure(text=t('ui.size',size=size))
        except (OSError,ValueError,AttributeError,TypeError):
            self.description.set(str(self.result))
            self.result_size.configure(text='')
        self.status.set(t('ui.previous') if previous else t('ui.saved'))
        self.dot.configure(fg=ACCENT)
        self.result_row.pack(fill='x',pady=(14,0),before=self.more)

    def open_folder(self):
        try:open_path(self.result)
        except OSError as exc:self.set_error(t('ui.open_failed',error=exc))

    def open_model(self):
        target=self.result/'model.blend'
        if not target.is_file():target=self.result/'model.glb'
        try:open_path(target)
        except OSError:
            self.description.set(t('ui.no_glb_app'))
            self.open_folder()

    def toggle_details(self):
        self.details_open=not self.details_open
        if self.details_open:
            self.log_box.pack(fill='both',expand=True,after=self.more,pady=(4,0))
            self.geometry(f'{self.winfo_width()}x{min(self.winfo_screenheight()-70,self.winfo_height()+180)}')
        else:
            self.log_box.pack_forget()
            self.geometry(f'{self.winfo_width()}x{max(680,self.winfo_height()-180)}')
        self.more.configure(text=t('ui.hide_details') if self.details_open else t('ui.details'))

    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='log':self.append_log(value);self.update_stage(value)
                elif kind=='done':
                    self.set_busy(False);self.result=Path(value);self.show_result();self.save_settings()
                elif kind=='error':
                    self.set_busy(False);self.set_error(value[0]);self.append_log(value[1])
                else:self.handle_update_event(kind,value)
        except queue.Empty:pass
        if self.running:
            seconds=int(time.monotonic()-self.started)
            self.elapsed.set(f'{seconds//60}:{seconds%60:02}')
        self.after(120,self.poll)

    def refresh_controls(self):
        busy=self.running or self.update_busy
        for widget in (self.url_entry,self.folder_entry,self.paste_button,self.browse_button,self.download_button):
            widget.configure(state='disabled' if busy else 'normal')
        self.language_selector.configure(state='disabled' if busy else 'readonly')
        if self.update_window and self.update_window.winfo_exists():
            for widget in self.update_controls:
                widget.configure(state='disabled' if busy or self.update_checking else 'normal')
            self.install_button.configure(state='normal' if self.available_release and not busy and not self.update_checking else 'disabled')
            if self.update_busy or self.update_checking:self.update_progress.start(15)
            else:self.update_progress.stop()

    def change_language(self,_event=None):
        set_language(LANGUAGES[self.language_names.index(self.language_var.get())])
        alive=[]
        for widget,message in self.translated_widgets:
            if widget.winfo_exists():
                widget.configure(text=render(message));alive.append((widget,message))
        self.translated_widgets=alive
        for variable in (self.status,self.description,self.update_text):variable.retranslate()
        self.more.configure(text=t('ui.hide_details') if self.details_open else t('ui.details'))
        self.log.configure(state='normal');self.log.delete('1.0','end')
        for message in self.log_messages:self.log.insert('end',str(render(message))+'\n')
        self.log.configure(state='disabled')
        if self.result and self.result_row.winfo_ismapped():self.show_result(previous=True)
        if self.update_window and self.update_window.winfo_exists():self.update_window.title(t('update.title'))
        if self.available_release:self.update_notice.configure(text=t('update.available',version=self.available_release.version))
        self.save_settings()
        self.refresh_controls()

    def show_updates(self):
        if self.update_window and self.update_window.winfo_exists():
            self.update_window.lift();return
        window=self.update_window=tk.Toplevel(self)
        window.title(t('update.title'));window.configure(bg=PANEL)
        window.geometry('690x420');window.minsize(650,420)
        panel=tk.Frame(window,bg=PANEL,padx=22,pady=18);panel.pack(fill='both',expand=True)
        self.label(panel,t('update.version',version=VERSION),font=('Segoe UI',13,'bold')).pack(anchor='w')
        self.label(panel,t('update.repository'),font=('Segoe UI',9),wraplength=620).pack(anchor='w',pady=(5,14))
        self.label(panel,t('update.public_hint'),font=('Segoe UI',10),wraplength=620,justify='left').pack(anchor='w')
        checkbox=ttk.Checkbutton(panel,text=t('update.auto_check'),variable=self.auto_updates,command=self.save_settings)
        checkbox.pack(anchor='w',pady=(12,0));self.translated_widgets.append((checkbox,t('update.auto_check')))
        self.update_controls=[checkbox]
        self.label(panel,textvariable=self.update_text,font=('Segoe UI',10),wraplength=620,
                   justify='left').pack(fill='x',pady=(12,8))
        self.update_progress=ttk.Progressbar(panel,mode='indeterminate',style='Local.Horizontal.TProgressbar')
        self.update_progress.pack(fill='x',pady=(0,12))
        row=tk.Frame(panel,bg=PANEL);row.pack(fill='x')
        check=self.button(row,t('update.check'),self.check_updates)
        check.pack(side='left');self.update_controls.append(check)
        self.install_button=self.button(panel,t('update.install'),self.install_update,primary=True)
        self.install_button.pack(fill='x',pady=(12,8))
        self.button(panel,t('update.open_release'),lambda:open_url(self.available_release.page_url if self.available_release else RELEASE_PAGE)).pack(anchor='w')
        self.refresh_controls()

    def check_updates(self,automatic=False):
        if self.running or self.update_busy or self.update_checking:return
        self.available_release=None
        self.update_checking=True
        self.update_busy=not automatic
        self.update_text.set(t('update.checking'));self.refresh_controls()
        def check():
            try:
                release=ReleaseClient().latest()
                self.events.put(('update_checked',release))
            except UpdateError as exc:self.events.put(('update_error',t(exc.key)))
            except Exception:self.events.put(('update_error',t('update.generic_error')))
        threading.Thread(target=check,daemon=True).start()

    def startup_update_check(self):
        threading.Thread(target=cleanup_completed_updates,daemon=True).start()
        if self.auto_updates.get():self.check_updates(automatic=True)

    def install_update(self):
        if self.running or self.update_busy or not self.available_release:return
        release=self.available_release
        self.update_busy=True;self.refresh_controls()
        def prepare():
            try:
                manifest=prepare_update(release,ReleaseClient(),lambda message:self.events.put(('update_progress',message)))
                self.events.put(('update_prepared',manifest))
            except UpdateError as exc:self.events.put(('update_error',t(exc.key)))
            except Exception:self.events.put(('update_error',t('update.generic_error')))
        threading.Thread(target=prepare,daemon=True).start()

    def handle_update_event(self,kind,value):
        if kind=='update_progress':self.update_text.set(value);return
        self.update_busy=False
        self.update_checking=False
        if kind=='update_checked':
            self.available_release=value
            message=t('update.available',version=self.available_release.version) if self.available_release else t('update.current',version=VERSION)
            self.update_text.set(message)
            if self.available_release:self.update_notice.configure(text=message)
            else:self.update_notice.configure(text='')
        elif kind=='update_error':
            self.update_text.set(value)
        elif kind=='update_prepared':
            self.update_text.set(t('update.restarting'));self.save_settings()
            try:start_installer(value)
            except OSError:self.update_text.set(t('update.install_failed'))
            else:self.destroy();return
        self.refresh_controls()

    def read_update_result(self):
        path=settings_directory()/'update-result.json'
        try:
            result=json.loads(path.read_text(encoding='utf-8'))
            if result.get('status')=='success':message=t('update.success',version=result['version'])
            else:message=t(result['key'],backup=result.get('backup',''))
            self.update_text.set(message);self.update_notice.configure(text=message)
            path.unlink()
        except (OSError,ValueError,KeyError):pass

    def on_close(self):
        if self.running or self.update_busy:
            self.description.set(t('ui.busy_close'))
            return
        self.save_settings()
        self.destroy()


def launch(receipt=None,language=None):
    Downloader(receipt=receipt,language=language).mainloop()


if __name__=='__main__':
    launch()
