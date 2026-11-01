package org.weinberg.wastewater.ui

import android.Manifest
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.weinberg.wastewater.data.Cities

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)
        setContent { WastewaterTheme { Root(vm, requestNotifications = { askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS) }) } }
    }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleIntent(intent) }

    private fun handleIntent(i: Intent?) {
        i?.getStringExtra("city")?.takeIf { it in Cities.all }?.let { vm.selectCity(it) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Root(vm: MainViewModel, requestNotifications: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val pathogen by vm.selectedPathogen.collectAsStateWithLifecycle()
    var showSettings by rememberSaveable { mutableStateOf(false) }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(if (showSettings) "Settings" else (state.city?.cityName ?: "Wastewater Watch")) },
            navigationIcon = {
                if (showSettings) IconButton(onClick = { showSettings = false }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            },
            actions = {
                if (!showSettings) {
                    IconButton(onClick = vm::refresh, enabled = !state.refreshing) { Icon(Icons.Default.Refresh, "Refresh") }
                    IconButton(onClick = { showSettings = true }) { Icon(Icons.Default.Settings, "Settings") }
                }
            })
    }) { pad ->
        AnimatedContent(targetState = showSettings, modifier = Modifier.padding(pad), label = "screen") { settings ->
            if (settings) SettingsScreen(state, vm, requestNotifications)
            else DashboardScreen(state, pathogen, onPathogen = { vm.selectedPathogen.value = it }, onCity = vm::selectCity, onRefresh = vm::refresh)
        }
    }
}
