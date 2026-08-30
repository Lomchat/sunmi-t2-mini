package website.chalco.sunmiprinter;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.RemoteException;

import com.sunmi.peripheral.printer.InnerPrinterCallback;
import com.sunmi.peripheral.printer.InnerPrinterException;
import com.sunmi.peripheral.printer.InnerPrinterManager;
import com.sunmi.peripheral.printer.InnerResultCallback;
import com.sunmi.peripheral.printer.SunmiPrinterService;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

final class PrinterClient {
    private final Context context;
    private volatile SunmiPrinterService service;
    private volatile String status = "connecting";
    private CountDownLatch readyLatch = new CountDownLatch(1);

    private final InnerPrinterCallback callback = new InnerPrinterCallback() {
        @Override protected void onConnected(SunmiPrinterService printerService) {
            service = printerService;
            status = "ready";
            readyLatch.countDown();
        }

        @Override protected void onDisconnected() {
            service = null;
            status = "disconnected";
            readyLatch = new CountDownLatch(1);
        }
    };

    PrinterClient(Context context) {
        this.context = context.getApplicationContext();
    }

    void bind() {
        try {
            boolean accepted = InnerPrinterManager.getInstance().bindService(context, callback);
            if (!accepted) status = "not_found";
        } catch (InnerPrinterException exception) {
            status = "error";
        }
    }

    String getStatus() {
        return status;
    }

    synchronized void print(JSONObject payload, ServerClient serverClient) throws Exception {
        if (service == null && !readyLatch.await(12, TimeUnit.SECONDS)) {
            throw new IllegalStateException("Service imprimante SUNMI indisponible");
        }
        final SunmiPrinterService printer = service;
        if (printer == null) throw new IllegalStateException("Imprimante déconnectée");

        JSONArray blocks = payload.getJSONArray("blocks");
        CountDownLatch resultLatch = new CountDownLatch(1);
        final boolean[] success = {false};
        final String[] message = {"Aucun résultat retourné par l’imprimante"};
        InnerResultCallback resultCallback = new InnerResultCallback() {
            @Override public void onRunResult(boolean isSuccess) throws RemoteException { }
            @Override public void onReturnString(String result) throws RemoteException { }
            @Override public void onRaiseException(int code, String msg) throws RemoteException {
                message[0] = "Erreur imprimante " + code + ": " + msg;
                resultLatch.countDown();
            }
            @Override public void onPrintResult(int code, String msg) throws RemoteException {
                success[0] = code == 0;
                message[0] = msg == null ? "Résultat " + code : msg;
                resultLatch.countDown();
            }
        };

        printer.enterPrinterBuffer(true);
        printer.printerInit(null);
        for (int index = 0; index < blocks.length(); index++) {
            JSONObject block = blocks.getJSONObject(index);
            String type = block.getString("type");
            if ("text".equals(type)) {
                printer.setAlignment(alignment(block.optString("align", "left")), null);
                printer.setFontSize((float) block.optDouble("size", 28), null);
                boolean bold = block.optBoolean("bold", false);
                printer.sendRAWData(new byte[]{0x1b, 0x45, (byte) (bold ? 1 : 0)}, null);
                printer.printText(block.getString("value") + "\n", null);
                printer.sendRAWData(new byte[]{0x1b, 0x45, 0}, null);
            } else if ("image".equals(type)) {
                printer.setAlignment(alignment(block.optString("align", "center")), null);
                Bitmap bitmap = serverClient.downloadAsset(block.getString("asset_id"));
                printer.printBitmap(bitmap, null);
                printer.lineWrap(1, null);
            } else if ("qr".equals(type)) {
                printer.setAlignment(alignment(block.optString("align", "center")), null);
                int moduleSize = Math.max(3, Math.min(12, block.optInt("module_size", 7)));
                printer.printQRCode(block.getString("value"), moduleSize, 3, null);
                printer.lineWrap(1, null);
            } else if ("feed".equals(type)) {
                printer.lineWrap(Math.max(0, Math.min(12, block.optInt("lines", 3))), null);
            } else if ("cut".equals(type)) {
                printer.cutPaper(null);
            }
        }
        printer.exitPrinterBufferWithCallback(true, resultCallback);
        if (!resultLatch.await(45, TimeUnit.SECONDS)) throw new IllegalStateException("Délai d’impression dépassé");
        if (!success[0]) throw new IllegalStateException(message[0]);
    }

    private int alignment(String value) {
        if ("center".equals(value)) return 1;
        if ("right".equals(value)) return 2;
        return 0;
    }
}

