# CloudEdge Home Assistant Integration

An Home Assistant integration for CloudEdge cameras. This integration provides control and monitoring of your CloudEdge devices through Home Assistant.
The primary purpose of this integration is to enable **automation control** for your CloudEdge cameras. By integrating with Home Assistant, you can create powerful automations to manage your cameras based on your automations and routines such as automatically enable **motion detection** when the "Away from Home" mode is activated or nobody is detected at home.

> **Disclaimer**: This integration is currently in **beta**. While it provides an interface for interacting with CloudEdge cameras, there are some known and unknown issues (see the Beta Notice section) that will be addressed in future versions.

## Support the Project

If you find this library useful, consider supporting its development! Your contributions help maintain and improve the project.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/I3I71LBUUU)

## Features

- 🎥 **Camera Integration**: Full camera entity support with device information
- 📊 **Sensor Monitoring**: Battery levels, WiFi strength, motion sensitivity, and more
- 🔧 **Device Control**: Switch entities for lights, motion detection, LED status, and notifications
- � **Complete Parameter Access**: All device parameters exposed as entities (disabled by default)
- �🔄 **Auto-refresh**: Configurable refresh intervals to keep device status current
- 🏠 **Multi-home Support**: Supports devices across multiple homes/locations

## Important note about CloudEdge sessions

CloudEdge allows only **one active session per account**. If you log in to this integration using your main account, the CloudEdge app on your phone will be logged out. 

### Recommendation:
To avoid disruptions, it is recommended to:
1. Create a **second CloudEdge account**.
2. Share access to your homes/devices with this second account.
3. Use the second account credentials for this integration.

This ensures that your main account remains logged in on your phone while the integration operates independently.

## Installation

### Method 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner and select "Custom repositories"
4. Add `https://github.com/fradaloisio/cloudedge-ha` as a custom repository
5. Select "Integration" as the category
6. Click "Add"
7. Search for "CloudEdge" and install
8. Restart Home Assistant

### Method 2: Manual Installation

1. Download the latest release from GitHub
2. Extract the files to your Home Assistant `custom_components` directory:
   ```
   custom_components/cloudedge/
   ```
3. Restart Home Assistant


## Enable Debug Logging

Add this to your `configuration.yaml` to enable detailed logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.cloudedge: debug
    cloudedge: debug
```

Then restart Home Assistant and check the logs for detailed information.

## Beta Notice

This integration is currently in **beta**. While it provides an interface for interacting with CloudEdge cameras, there are some known and unknown issues that will be addressed in future versions:

- **Status Reliability**: The API always shows the camera as online, which may not reflect the actual status.
- **Refresh Reliability**: The API refreshes only after some time when the CloudEdge app is not opened on the phone. This does not impact device control.
- **Regional Support**: This integration now supports both European and US accounts. By default the integration attempts to detect the region based on the `country_code` you provide; you can override the region or explicitly specify `BASE_URL` and `OPENAPI_BASE_URL` when configuring the integration if necessary. Work continues to support automatic discovery and additional regions.

We appreciate your understanding and welcome feedback to improve the library.