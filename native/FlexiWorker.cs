using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;

// A separate x86 process owns the legacy native library. The controller applies
// a timeout; no native function is loaded in the UI or Python process.
internal static class FlexiWorker {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr LoadLibraryW(string name);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, SetLastError=true)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern bool SetDllDirectoryW(string path);
    [DllImport("kernel32.dll")] private static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int ConvertFile(IntPtr input, IntPtr output, out double width, out double height);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int SetRuntimeErrorMode(int mode);

    private static int Main(string[] args) {
        string stage = null;
        try {
            if (args.Length != 3) throw new ArgumentException("Usage: FlexiWorker input.fs output.eps flexi_install_root");
            if (IntPtr.Size != 4) throw new InvalidOperationException("x86 worker required");
            string input = Path.GetFullPath(args[0]);
            string output = Path.GetFullPath(args[1]);
            string root = Path.GetFullPath(args[2]);
            string program = Path.Combine(root, "Program");
            string lib = Path.Combine(program, "fs2eps.dll");
            if (!File.Exists(input) || !File.Exists(lib)) throw new FileNotFoundException("Input or fs2eps.dll missing");
            stage = Path.Combine(Path.GetTempPath(), "livery_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(stage);
            string stagedInput = Path.Combine(stage, "source.fs");
            string stagedOutput = Path.Combine(stage, "full.eps");
            File.Copy(input, stagedInput, false);
            string search = program + ";" + Path.Combine(root,"Plugins") + ";" + Path.Combine(root,"Filters") + ";" + root;
            Environment.SetEnvironmentVariable("PATH", search + ";" + Environment.GetEnvironmentVariable("PATH"));
            SetDllDirectoryW(program);
            SetErrorMode(0x0001 | 0x0002 | 0x8000);
            // Legacy filter registration resolves sibling plugin directories
            // from the application directory, so retain the installed layout.
            Directory.SetCurrentDirectory(program);
            Console.Error.WriteLine("loading native converter");
            IntPtr crt = LoadLibraryW(Path.Combine(program,"MSVCR71.dll"));
            if(crt != IntPtr.Zero) {
                IntPtr setter = GetProcAddress(crt,"_set_error_mode");
                if(setter != IntPtr.Zero)
                    ((SetRuntimeErrorMode)Marshal.GetDelegateForFunctionPointer(setter,typeof(SetRuntimeErrorMode)))(2);
            }
            IntPtr module = LoadLibraryW(lib);
            if (module == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "LoadLibrary fs2eps.dll");
            IntPtr symbol = GetProcAddress(module,"Flexi2EPS");
            if (symbol == IntPtr.Zero) throw new EntryPointNotFoundException("Flexi2EPS");
            ConvertFile convert = (ConvertFile)Marshal.GetDelegateForFunctionPointer(symbol, typeof(ConvertFile));
            IntPtr a = Marshal.StringToHGlobalAnsi(stagedInput);
            IntPtr b = Marshal.StringToHGlobalAnsi(stagedOutput);
            double width, height;
            int status;
            try {
                Console.Error.WriteLine("converting staged FS");
                status = convert(a,b,out width,out height);
            } finally { Marshal.FreeHGlobal(a); Marshal.FreeHGlobal(b); }
            if (!File.Exists(stagedOutput) || new FileInfo(stagedOutput).Length < 20)
                throw new InvalidOperationException("Converter produced no EPS; native return=" + status);
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            File.Copy(stagedOutput,output,true);
            Console.WriteLine(new JavaScriptSerializer().Serialize(new {
                ok=true, native_return=status, width_raw=width, height_raw=height,
                physical_scale_verified=false, output=output, bytes=new FileInfo(output).Length
            }));
            return 0;
        } catch (Exception e) {
            Console.WriteLine(new JavaScriptSerializer().Serialize(new {ok=false,error=e.GetType().Name + ": " + e.Message}));
            return 2;
        } finally {
            if(stage != null) { try { Directory.Delete(stage,true); } catch {} }
        }
    }
}
