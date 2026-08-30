package website.chalco.sunmiprinter;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.widget.Button;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler();
    private TextView statusView;
    private TextView detailView;

    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            SharedPreferences preferences = getSharedPreferences(PrintAgentService.PREFS, MODE_PRIVATE);
            String status = preferences.getString("status", "Initialisation…");
            String error = preferences.getString("last_error", "");
            long lastContact = preferences.getLong("last_contact", 0L);
            statusView.setText(status);
            StringBuilder detail = new StringBuilder();
            detail.append("Serveur : ").append(BuildConfig.SERVER_URL).append('\n');
            detail.append("Appareil : ").append(BuildConfig.DEVICE_ID).append('\n');
            if (lastContact > 0) {
                long seconds = Math.max(0, (System.currentTimeMillis() - lastContact) / 1000);
                detail.append("Dernier contact : il y a ").append(seconds).append(" s");
            } else {
                detail.append("Dernier contact : jamais");
            }
            if (!error.isEmpty()) detail.append("\nDernière erreur : ").append(error);
            detailView.setText(detail.toString());
            handler.postDelayed(this, 1000);
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        statusView = findViewById(R.id.status);
        detailView = findViewById(R.id.detail);
        Button restart = findViewById(R.id.restart);
        Button dashboard = findViewById(R.id.dashboard);
        restart.setOnClickListener(view -> {
            stopService(new Intent(this, PrintAgentService.class));
            startAgent();
        });
        dashboard.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(BuildConfig.SERVER_URL))));
        startAgent();
    }

    private void startAgent() {
        Intent intent = new Intent(this, PrintAgentService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent);
        else startService(intent);
    }

    @Override protected void onResume() {
        super.onResume();
        handler.post(refresh);
    }

    @Override protected void onPause() {
        handler.removeCallbacks(refresh);
        super.onPause();
    }
}

