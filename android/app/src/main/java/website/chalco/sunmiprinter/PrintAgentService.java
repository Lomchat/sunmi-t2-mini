package website.chalco.sunmiprinter;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.LinkedHashSet;
import java.util.Set;

public final class PrintAgentService extends Service {
    static final String PREFS = "sunmi_print_agent";
    private static final String CHANNEL_ID = "sunmi_print_agent_channel";
    private static final int NOTIFICATION_ID = 8701;
    private volatile boolean running;
    private Thread worker;
    private PrinterClient printer;
    private ServerClient server;
    private SharedPreferences preferences;
    private PowerManager.WakeLock wakeLock;

    @Override public void onCreate() {
        super.onCreate();
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        startForeground(NOTIFICATION_ID, buildNotification());
        PowerManager manager = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sunmi:print-agent");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
        printer = new PrinterClient(this);
        printer.bind();
        server = new ServerClient(BuildConfig.SERVER_URL, BuildConfig.DEVICE_ID, BuildConfig.DEVICE_TOKEN);
        startWorker();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (worker == null || !worker.isAlive()) startWorker();
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }

    private synchronized void startWorker() {
        if (worker != null && worker.isAlive()) return;
        running = true;
        worker = new Thread(this::runLoop, "sunmi-print-agent");
        worker.start();
    }

    private void runLoop() {
        int failures = 0;
        setState("Connexion au serveur…", "", false);
        while (running) {
            try {
                String lastError = preferences.getString("last_error", "");
                JSONObject job = server.nextJob(printer.getStatus(), lastError);
                failures = 0;
                touchContact();
                if (job == null) {
                    setState("En ligne · en attente d’impression", "", true);
                    continue;
                }
                String jobId = job.getString("id");
                String leaseToken = job.getString("lease_token");
                if (wasCompleted(jobId)) {
                    server.acknowledge(jobId, leaseToken, true, "");
                    continue;
                }
                setState("Impression en cours…", "", true);
                try {
                    printer.print(job.getJSONObject("payload"), server);
                    rememberCompleted(jobId);
                    server.acknowledge(jobId, leaseToken, true, "");
                    setState("Impression terminée · en attente", "", true);
                } catch (Exception printError) {
                    String message = friendly(printError);
                    try { server.acknowledge(jobId, leaseToken, false, message); } catch (Exception ignored) { }
                    setState("Échec de l’impression", message, true);
                    sleep(3000);
                }
            } catch (Exception networkError) {
                failures++;
                String message = friendly(networkError);
                setState("Serveur inaccessible · nouvelle tentative", message, false);
                sleep(Math.min(30_000, 2_000L * failures));
            }
        }
    }

    private void setState(String status, String error, boolean online) {
        SharedPreferences.Editor editor = preferences.edit().putString("status", status).putString("last_error", error);
        if (online) editor.putLong("last_contact", System.currentTimeMillis());
        editor.apply();
    }

    private void touchContact() {
        preferences.edit().putLong("last_contact", System.currentTimeMillis()).apply();
    }

    private boolean wasCompleted(String id) {
        return readCompleted().contains(id);
    }

    private void rememberCompleted(String id) {
        Set<String> values = readCompleted();
        values.add(id);
        while (values.size() > 100) values.remove(values.iterator().next());
        JSONArray array = new JSONArray();
        for (String value : values) array.put(value);
        preferences.edit().putString("completed_jobs", array.toString()).apply();
    }

    private Set<String> readCompleted() {
        LinkedHashSet<String> values = new LinkedHashSet<>();
        try {
            JSONArray array = new JSONArray(preferences.getString("completed_jobs", "[]"));
            for (int index = 0; index < array.length(); index++) values.add(array.getString(index));
        } catch (Exception ignored) { }
        return values;
    }

    private Notification buildNotification() {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, getString(R.string.service_channel), NotificationManager.IMPORTANCE_LOW);
            channel.setDescription(getString(R.string.service_running));
            manager.createNotificationChannel(channel);
        }
        Intent launchIntent = new Intent(this, MainActivity.class);
        int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) pendingFlags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 0, launchIntent, pendingFlags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(R.drawable.ic_printer)
                .setContentTitle(getString(R.string.agent_title))
                .setContentText(getString(R.string.service_running))
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .build();
    }

    private static String friendly(Exception exception) {
        String message = exception.getMessage();
        if (message == null || message.trim().isEmpty()) message = exception.getClass().getSimpleName();
        return message.length() > 500 ? message.substring(0, 500) : message;
    }

    private void sleep(long millis) {
        try { Thread.sleep(millis); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
    }

    @Override public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }
}

