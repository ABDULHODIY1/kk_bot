import customtkinter as ctk
from tkinter import filedialog, messagebox
import subprocess
import threading
import os
from PIL import Image

ctk.set_appearance_mode("dark")

class SuperFastResizer(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Kino TV - Ultra Fast Resizer")
        self.geometry("500x400")

        # Ikonka (PNG bo'lsa)
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'icon.jpg')
            pil_img = Image.open(icon_path)
            self.icon_image = ctk.CTkImage(pil_img, size=(80, 80))
            self.icon_label = ctk.CTkLabel(self, text="", image=self.icon_image)
            self.icon_label.pack(pady=10)
        except: pass

        self.label = ctk.CTkLabel(self, text="ULTRA FAST 720P CONVERTER", font=("Arial", 20, "bold"))
        self.label.pack(pady=10)

        self.status_label = ctk.CTkLabel(self, text="Video tanlang", text_color="gray")
        self.status_label.pack(pady=5)

        self.select_btn = ctk.CTkButton(self, text="FAYLNI TANLASH", command=self.select_file)
        self.select_btn.pack(pady=15)

        self.convert_btn = ctk.CTkButton(self, text="TEZKOR O'TKAZISH", command=self.start_conversion, 
                                         state="disabled", fg_color="#2ecc71", text_color="black")
        self.convert_btn.pack(pady=10)

        self.input_path = ""

    def select_file(self):
        self.input_path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi")])
        if self.input_path:
            self.status_label.configure(text=f"Tanlandi: {os.path.basename(self.input_path)}", text_color="#3498db")
            self.convert_btn.configure(state="normal")

    def convert_video(self):
        try:
            self.convert_btn.configure(state="disabled")
            self.status_label.configure(text="Jarayon ketmoqda... (FFmpeg)", text_color="#f1c40f")
            
            output_path = os.path.splitext(self.input_path)[0] + "_fast_720p.mp4"
            
            # FFmpeg buyrug'i: MoviePy'dan ko'ra ancha tez ishlaydi
            # -preset ultrafast: Protsessorni qiynamasdan eng tez usulda siqish
            # -vf scale=-1:720: Bo'yini 720 qilish, enini avtomatik moslash
            command = [
                'ffmpeg', '-i', self.input_path,
                '-vf', 'scale=-1:720',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                '-c:a', 'copy', # Ovozni qayta ishlamaydi (vaqtni tejaydi)
                output_path, '-y'
            ]
            
            # Jarayonni ishga tushirish
            process = subprocess.run(command, capture_output=True, text=True)
            
            if process.returncode == 0:
                messagebox.showinfo("Tayyor!", f"Video tezkor saqlandi!\n{output_path}")
                self.status_label.configure(text="Muvaffaqiyatli!", text_color="#2ecc71")
            else:
                raise Exception(process.stderr)

        except Exception as e:
            messagebox.showerror("Xato", f"FFmpeg topilmadi yoki xato: {str(e)}")
            self.status_label.configure(text="Xatolik!", text_color="red")
        finally:
            self.convert_btn.configure(state="normal")

    def start_conversion(self):
        threading.Thread(target=self.convert_video, daemon=True).start()

if __name__ == "__main__":
    app = SuperFastResizer()
    app.mainloop()