import json
import os
import sys
import tkinter as tk

BG_BAR = "#121212"
BG_PANEL = "#1e1e1e"
BG_CONTROL = "#333333"
FG_TEXT = "#ffffff"
FG_MUTED = "#888888"
ACCENT = "#ff6600"
DANGER = "#ff0000"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 14, "bold")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.json")


def dark_title_bar(window):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result == 0:
                break
    except Exception:
        pass


def blank_icon(window):
    try:
        icon = tk.PhotoImage(width=16, height=16)
        icon.put(BG_BAR, to=(0, 0, 16, 16))
        window.iconphoto(True, icon)
        window._blank_icon = icon
    except Exception:
        pass


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"projects": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            return data
    except Exception:
        pass
    return {"projects": []}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_button(parent, text, command, color=FG_TEXT):
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=BG_CONTROL,
        fg=color,
        activebackground=BG_CONTROL,
        activeforeground=ACCENT,
        disabledforeground=FG_MUTED,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BG_BAR,
        highlightcolor=ACCENT,
        font=FONT,
        padx=10,
        pady=4,
        cursor="hand2",
    )


class ItemDialog(tk.Toplevel):
    def __init__(self, parent, title, name="", description=""):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.configure(bg=BG_BAR)
        self.transient(parent)
        self.resizable(True, True)
        self.minsize(360, 260)

        body = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(body, text="Name", bg=BG_PANEL, fg=FG_TEXT, font=FONT).pack(anchor="w", padx=10, pady=(10, 2))
        self.name_entry = tk.Entry(
            body,
            bg=BG_CONTROL,
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BG_BAR,
            highlightcolor=ACCENT,
            font=FONT,
        )
        self.name_entry.pack(fill="x", padx=10)
        self.name_entry.insert(0, name)

        tk.Label(body, text="Description", bg=BG_PANEL, fg=FG_TEXT, font=FONT).pack(anchor="w", padx=10, pady=(10, 2))
        self.desc_text = tk.Text(
            body,
            height=6,
            bg=BG_CONTROL,
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BG_BAR,
            highlightcolor=ACCENT,
            font=FONT,
            wrap="word",
        )
        self.desc_text.pack(fill="both", expand=True, padx=10)
        self.desc_text.insert("1.0", description)

        self.error_label = tk.Label(body, text="", bg=BG_PANEL, fg=ACCENT, font=FONT)
        self.error_label.pack(anchor="w", padx=10, pady=(4, 0))

        buttons = tk.Frame(self, bg=BG_BAR)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        make_button(buttons, "Cancel", self.destroy).pack(side="right")
        make_button(buttons, "Save", self.on_save).pack(side="right", padx=(0, 6))

        self.bind("<Escape>", lambda e: self.destroy())
        self.name_entry.bind("<Return>", lambda e: self.on_save())

        dark_title_bar(self)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.name_entry.focus_set()
        self.wait_window()

    def on_save(self):
        name = self.name_entry.get().strip()
        if not name:
            self.error_label.config(text="Name is required")
            return
        self.result = {"name": name, "description": self.desc_text.get("1.0", "end").strip()}
        self.destroy()


class ConfirmDialog(tk.Toplevel):
    def __init__(self, parent, message):
        super().__init__(parent)
        self.result = False
        self.title("Confirm Delete")
        self.configure(bg=BG_BAR)
        self.transient(parent)

        body = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(body, text=message, bg=BG_PANEL, fg=FG_TEXT, font=FONT, wraplength=320, justify="left").pack(
            padx=16, pady=16
        )

        buttons = tk.Frame(self, bg=BG_BAR)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        make_button(buttons, "Cancel", self.destroy).pack(side="right")
        make_button(buttons, "Delete", self.on_delete, DANGER).pack(side="right", padx=(0, 6))

        self.bind("<Escape>", lambda e: self.destroy())

        dark_title_bar(self)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window()

    def on_delete(self):
        self.result = True
        self.destroy()


class Column:
    def __init__(self, parent, title, on_select, on_add, on_edit, on_delete):
        self.frame = tk.Frame(parent, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)

        tk.Label(self.frame, text=title, bg=BG_PANEL, fg=ACCENT, font=FONT_BOLD).pack(anchor="w", padx=10, pady=(8, 4))

        list_frame = tk.Frame(self.frame, bg=BG_PANEL)
        list_frame.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(
            list_frame,
            bg=BG_PANEL,
            fg=FG_TEXT,
            selectbackground=BG_CONTROL,
            selectforeground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BG_BAR,
            highlightcolor=ACCENT,
            activestyle="none",
            exportselection=False,
            font=FONT,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(
            list_frame,
            command=self.listbox.yview,
            bg=BG_CONTROL,
            troughcolor=BG_BAR,
            activebackground=BG_CONTROL,
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", lambda e: on_select())
        self.listbox.bind("<Double-Button-1>", lambda e: on_edit())

        buttons = tk.Frame(self.frame, bg=BG_PANEL)
        buttons.pack(fill="x", padx=10, pady=8)
        self.add_button = make_button(buttons, "Add", on_add)
        self.add_button.pack(side="left")
        self.edit_button = make_button(buttons, "Edit", on_edit)
        self.edit_button.pack(side="left", padx=6)
        self.delete_button = make_button(buttons, "Delete", on_delete, DANGER)
        self.delete_button.pack(side="left")

    def fill(self, items, selected=None):
        self.listbox.delete(0, "end")
        for item in items:
            self.listbox.insert("end", item["name"])
        if selected is not None and 0 <= selected < len(items):
            self.listbox.selection_set(selected)
            self.listbox.see(selected)

    def selected_index(self):
        selection = self.listbox.curselection()
        return selection[0] if selection else None

    def set_enabled(self, can_add, has_selection):
        self.add_button.config(state="normal" if can_add else "disabled")
        state = "normal" if has_selection else "disabled"
        self.edit_button.config(state=state)
        self.delete_button.config(state=state, fg=DANGER if has_selection else FG_MUTED)


class ProjectManager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Project Manager")
        self.configure(bg=BG_BAR)
        self.geometry("1000x600")
        self.minsize(700, 400)
        blank_icon(self)

        self.data = load_data()
        self.project_index = None
        self.equipment_index = None
        self.component_index = None

        header = tk.Frame(self, bg=BG_BAR)
        header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(header, text="Project Manager", bg=BG_BAR, fg=ACCENT, font=FONT_TITLE).pack(side="left")
        make_button(header, "Quit", self.destroy, DANGER).pack(side="right")

        columns = tk.Frame(self, bg=BG_BAR)
        columns.pack(fill="both", expand=True, padx=12)
        for i in range(3):
            columns.columnconfigure(i, weight=1, uniform="col")
        columns.rowconfigure(0, weight=1)

        self.project_col = Column(
            columns, "Projects", self.on_project_select, self.add_project, self.edit_project, self.delete_project
        )
        self.equipment_col = Column(
            columns, "Equipment", self.on_equipment_select, self.add_equipment, self.edit_equipment, self.delete_equipment
        )
        self.component_col = Column(
            columns, "Components", self.on_component_select, self.add_component, self.edit_component, self.delete_component
        )
        self.project_col.frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.equipment_col.frame.grid(row=0, column=1, sticky="nsew", padx=4)
        self.component_col.frame.grid(row=0, column=2, sticky="nsew", padx=(4, 0))

        details = tk.Frame(self, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_BAR)
        details.pack(fill="x", padx=12, pady=(8, 0))
        self.details_title = tk.Label(details, text="", bg=BG_PANEL, fg=ACCENT, font=FONT_BOLD, anchor="w")
        self.details_title.pack(fill="x", padx=10, pady=(8, 2))
        self.details_text = tk.Label(
            details, text="", bg=BG_PANEL, fg=FG_TEXT, font=FONT, anchor="nw", justify="left", height=4
        )
        self.details_text.pack(fill="x", padx=10, pady=(0, 8))
        details.bind("<Configure>", lambda e: self.details_text.config(wraplength=max(e.width - 30, 100)))

        self.status = tk.Label(self, text="", bg=BG_BAR, fg=FG_MUTED, font=FONT, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(4, 8))

        self.refresh()
        dark_title_bar(self)

    def projects(self):
        return self.data["projects"]

    def current_project(self):
        if self.project_index is None:
            return None
        return self.projects()[self.project_index]

    def equipment_list(self):
        project = self.current_project()
        return project.setdefault("equipment", []) if project else []

    def current_equipment(self):
        if self.equipment_index is None:
            return None
        return self.equipment_list()[self.equipment_index]

    def component_list(self):
        equipment = self.current_equipment()
        return equipment.setdefault("components", []) if equipment else []

    def current_component(self):
        if self.component_index is None:
            return None
        return self.component_list()[self.component_index]

    def refresh(self):
        self.project_col.fill(self.projects(), self.project_index)
        self.equipment_col.fill(self.equipment_list(), self.equipment_index)
        self.component_col.fill(self.component_list(), self.component_index)

        self.project_col.set_enabled(True, self.project_index is not None)
        self.equipment_col.set_enabled(self.project_index is not None, self.equipment_index is not None)
        self.component_col.set_enabled(self.equipment_index is not None, self.component_index is not None)

        if self.component_index is not None:
            item, kind = self.current_component(), "Component"
        elif self.equipment_index is not None:
            item, kind = self.current_equipment(), "Equipment"
        elif self.project_index is not None:
            item, kind = self.current_project(), "Project"
        else:
            item, kind = None, ""

        if item:
            self.details_title.config(text=f"{kind}: {item['name']}")
            self.details_text.config(
                text=item.get("description") or "No description", fg=FG_TEXT if item.get("description") else FG_MUTED
            )
        else:
            self.details_title.config(text="Select a project")
            self.details_text.config(text="", fg=FG_TEXT)

        self.status.config(text=f"{len(self.projects())} projects  |  {DATA_FILE}")

    def persist(self):
        try:
            save_data(self.data)
        except Exception as e:
            self.status.config(text=f"Save failed: {e}")
            return
        self.refresh()

    def on_project_select(self):
        index = self.project_col.selected_index()
        if index is None or index == self.project_index:
            return
        self.project_index = index
        self.equipment_index = None
        self.component_index = None
        self.refresh()

    def on_equipment_select(self):
        index = self.equipment_col.selected_index()
        if index is None or index == self.equipment_index:
            return
        self.equipment_index = index
        self.component_index = None
        self.refresh()

    def on_component_select(self):
        index = self.component_col.selected_index()
        if index is None or index == self.component_index:
            return
        self.component_index = index
        self.refresh()

    def ask_item(self, title, item=None):
        dialog = ItemDialog(self, title, item["name"] if item else "", item.get("description", "") if item else "")
        return dialog.result

    def confirm(self, message):
        return ConfirmDialog(self, message).result

    def add_project(self):
        result = self.ask_item("Add Project")
        if not result:
            return
        result["equipment"] = []
        self.projects().append(result)
        self.project_index = len(self.projects()) - 1
        self.equipment_index = None
        self.component_index = None
        self.persist()

    def edit_project(self):
        project = self.current_project()
        if not project:
            return
        result = self.ask_item("Edit Project", project)
        if result:
            project.update(result)
            self.persist()

    def delete_project(self):
        project = self.current_project()
        if not project:
            return
        if self.confirm(f"Delete project '{project['name']}' and all its equipment and components?"):
            del self.projects()[self.project_index]
            self.project_index = None
            self.equipment_index = None
            self.component_index = None
            self.persist()

    def add_equipment(self):
        if self.current_project() is None:
            return
        result = self.ask_item("Add Equipment")
        if not result:
            return
        result["components"] = []
        self.equipment_list().append(result)
        self.equipment_index = len(self.equipment_list()) - 1
        self.component_index = None
        self.persist()

    def edit_equipment(self):
        equipment = self.current_equipment()
        if not equipment:
            return
        result = self.ask_item("Edit Equipment", equipment)
        if result:
            equipment.update(result)
            self.persist()

    def delete_equipment(self):
        equipment = self.current_equipment()
        if not equipment:
            return
        if self.confirm(f"Delete equipment '{equipment['name']}' and all its components?"):
            del self.equipment_list()[self.equipment_index]
            self.equipment_index = None
            self.component_index = None
            self.persist()

    def add_component(self):
        if self.current_equipment() is None:
            return
        result = self.ask_item("Add Component")
        if not result:
            return
        self.component_list().append(result)
        self.component_index = len(self.component_list()) - 1
        self.persist()

    def edit_component(self):
        component = self.current_component()
        if not component:
            return
        result = self.ask_item("Edit Component", component)
        if result:
            component.update(result)
            self.persist()

    def delete_component(self):
        component = self.current_component()
        if not component:
            return
        if self.confirm(f"Delete component '{component['name']}'?"):
            del self.component_list()[self.component_index]
            self.component_index = None
            self.persist()


if __name__ == "__main__":
    ProjectManager().mainloop()
