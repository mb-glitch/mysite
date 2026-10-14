package pl.mysite.dashboard

import android.app.Application

class DashboardApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        PushClient.channels(this)
    }
}
