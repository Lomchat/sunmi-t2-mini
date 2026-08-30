package website.chalco.sunmiprinter;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class ServerClient {
    private final String baseUrl;
    private final String deviceId;
    private final String token;

    ServerClient(String baseUrl, String deviceId, String token) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.deviceId = deviceId;
        this.token = token;
    }

    JSONObject nextJob(String printerStatus, String lastError) throws Exception {
        String path = "/api/device/jobs/next?device_id=" + deviceId + "&wait=25";
        HttpURLConnection connection = open(path, "GET", 35_000);
        connection.setRequestProperty("X-App-Version", BuildConfig.VERSION_NAME);
        connection.setRequestProperty("X-Device-Model", safeHeader(android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL));
        connection.setRequestProperty("X-Printer-Status", safeHeader(printerStatus));
        connection.setRequestProperty("X-Last-Error", safeHeader(lastError));
        int code = connection.getResponseCode();
        if (code == 204) {
            connection.disconnect();
            return null;
        }
        if (code != 200) throw new IOException("Le serveur a répondu " + code + ": " + readError(connection));
        String body = readText(connection.getInputStream());
        connection.disconnect();
        return new JSONObject(body);
    }

    void heartbeat(String printerStatus, String lastError) throws Exception {
        JSONObject body = new JSONObject();
        body.put("device_id", deviceId);
        body.put("app_version", BuildConfig.VERSION_NAME);
        body.put("device_model", android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL);
        body.put("printer_status", printerStatus);
        body.put("last_error", lastError);
        requestJson("/api/device/heartbeat", body);
    }

    void acknowledge(String jobId, String leaseToken, boolean success, String error) throws Exception {
        JSONObject body = new JSONObject();
        body.put("lease_token", leaseToken);
        body.put("status", success ? "printed" : "failed");
        body.put("error", error == null ? "" : error);
        requestJson("/api/device/jobs/" + jobId + "/result", body);
    }

    Bitmap downloadAsset(String assetId) throws Exception {
        HttpURLConnection connection = open("/api/device/assets/" + assetId, "GET", 20_000);
        int code = connection.getResponseCode();
        if (code != 200) throw new IOException("Téléchargement image refusé: HTTP " + code);
        Bitmap bitmap;
        try (InputStream input = new BufferedInputStream(connection.getInputStream())) {
            bitmap = BitmapFactory.decodeStream(input);
        } finally {
            connection.disconnect();
        }
        if (bitmap == null) throw new IOException("Image illisible");
        if (bitmap.getWidth() > 576) {
            int height = Math.max(1, Math.round(bitmap.getHeight() * 576f / bitmap.getWidth()));
            bitmap = Bitmap.createScaledBitmap(bitmap, 576, height, true);
        }
        return bitmap;
    }

    private void requestJson(String path, JSONObject body) throws Exception {
        HttpURLConnection connection = open(path, "POST", 20_000);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setDoOutput(true);
        try (OutputStream output = connection.getOutputStream()) {
            output.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }
        int code = connection.getResponseCode();
        if (code < 200 || code >= 300) throw new IOException("Le serveur a répondu " + code + ": " + readError(connection));
        connection.disconnect();
    }

    private HttpURLConnection open(String path, String method, int readTimeout) throws Exception {
        URL url = new URL(baseUrl + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(12_000);
        connection.setReadTimeout(readTimeout);
        connection.setUseCaches(false);
        connection.setRequestProperty("Authorization", "Bearer " + token);
        connection.setRequestProperty("Accept", "application/json");
        return connection;
    }

    private static String readError(HttpURLConnection connection) {
        try {
            InputStream stream = connection.getErrorStream();
            return stream == null ? "" : readText(stream);
        } catch (Exception ignored) {
            return "";
        }
    }

    private static String readText(InputStream stream) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
        StringBuilder result = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) result.append(line);
        return result.toString();
    }

    private static String safeHeader(String value) {
        if (value == null) return "";
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < value.length() && result.length() < 300; i++) {
            char c = value.charAt(i);
            result.append(c >= 32 && c <= 126 ? c : '?');
        }
        return result.toString();
    }
}

