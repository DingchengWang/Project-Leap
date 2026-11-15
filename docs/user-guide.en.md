# Project Leap user guide

[中文](user-guide.zh.md) · [Project home](../README.md) · [About](about.en.md)

This guide covers application installation, module management, saved analyses, and settings. For image preparation, analysis parameters, manual review, and module-specific outputs, use the module guides linked below.

**Release status:** this release provides the module packages, shared components, and component catalog. The public Project Leap 1.0.0 DMG remains in preparation. The installation steps below apply once the application download is available.

## 1. Install the application

Use an **Apple Silicon Mac running macOS 27 or later**. Internet access is required to download the application, install modules, and obtain updates.

1. Open the [project website](https://dingchengwang.github.io/Project-Leap/en/) or [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases).
2. Download `Project-Leap-1.0.0-macOS-arm64.dmg` when it is listed as available.
3. Open the DMG and drag **Project Leap.app** into **Applications**.
4. Open Project Leap from Applications. After copying the application, you can eject the DMG.

The public DMG contains the main application. Analysis modules and their dependencies are installed through Project Leap.

### If macOS blocks the first launch

The planned initial release does not have an Apple Developer ID signature or Apple notarization. Gatekeeper may therefore report that the developer cannot be verified or that Apple cannot check the application for malicious software.

If you downloaded the application from the project release and choose to open it:

1. Attempt to open **Project Leap.app** once, then dismiss the alert.
2. Open **System Settings → Privacy & Security** and scroll to the blocked-application message.
3. Click **Open Anyway**, then confirm **Open** in the next alert. Complete macOS authentication if requested.

macOS saves the exception for that application. These steps follow [Apple’s instructions for opening an unnotarized application](https://support.apple.com/en-us/102445). An alert saying the application **will damage your computer** or **is damaged** needs separate investigation; do not treat it as the unidentified-developer prompt. If a managed Mac does not allow the exception, contact its administrator.

## 2. Install an analysis module

1. Select **Modules & Maintenance** in the sidebar.
2. Find the module you want to use and click **Install analysis module**.
3. Keep the application open while it downloads, verifies, and installs the required components.
4. Wait until the module shows **Installed** and its installation button reads **Module ready**.

Install either module or both. The application manages the shared scientific runtime and Cellpose model; the fluorescence module also uses Fiji. You do not need to install Python, Cellpose, or Fiji separately. Shared dependencies already available to the application can be reused by the other module.

Installation and repair pause while analysis or its review processes are using the components. Finish those operations before maintaining a module.

## 3. Choose a module and run an analysis

Select an installed module in the sidebar to open its **Analysis Workspace**. Follow its guide through image selection, channel assignment, analysis, review, and saving:

| Module | Use it for | Guide |
|---|---|---|
| **Astrocyte Fluorescence Intensity Measurement** | Measure fluorescence within astrocyte regions of interest and review those regions in Fiji. | [Fluorescence guide](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md) |
| **Cell Counting** | Review candidate cells from DAPI and auxiliary-channel evidence, edit the count, and save the confirmed results. | [Cell Counting guide](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md) |

During an analysis, **Run details** shows progress and messages. You can switch to other pages or modules while an analysis runs; starting a second analysis must wait until the current one finishes. When the workflow requests manual review, complete it and save through the module’s controls. A completed calculation alone does not establish that a reviewed result has been saved.

## 4. View and manage saved analyses

Open **Jobs & Results**. Analyses are grouped by module. Click a row to expand **Job details**, where you can inspect the recorded status, result previews, and available output files.

- Click a preview or output filename to open the file. For a completed analysis, **Open results folder** opens its output directory.
- Expand **Run details** to inspect the saved software, inputs, parameters, review, and timing where recorded.
- For counting analyses, use **Reopen count** to edit a saved count or **Continue count review** to resume an available review session. The counting guide explains how edits are saved.
- Click **Refresh storage** to update the estimated disk space used by each analysis inside application storage.

To delete an analysis, use its trash button. To delete several, click **Select**, select the records, and click **Delete selected**. Read the confirmation before proceeding: deletion permanently removes those analyses’ application-managed data. Original images and files already exported outside application storage are retained. **Don’t ask again for one month** suppresses subsequent deletion confirmations until the calendar-month interval expires.

Keep original images available if you want to rerun an analysis or inspect original Z planes. Saved results and retained review data do not replace the source images. If a counting session reports that its source image has moved, use the relocation control in that session; the application checks the replacement against the saved analysis.

## 5. Adjust settings

### Language and appearance

Open **Settings → Language & appearance**. **Interface language** offers **English** and **中文**. **Appearance** offers **System**, **Light**, and **Dark**. The application’s appearance setting does not change scientific image colors; Fiji and pages opened in a browser use their own appearance settings.

### Storage and migration

**Settings → Storage & data** displays the active application data folder. **Open application data** opens it in Finder. This folder contains installed components, job records, review data, and saved results.

To move that data, finish active work, click **Change storage folder**, and select a **new, empty folder**. Let Project Leap migrate and verify the data, then complete the application restart. Use this control to change the managed location; moving the folder manually leaves the saved location pointing to the old path.

Original input images remain at their source locations. Moving application data does not move those images or external exports. If the application reports that its configured data folder is missing, reconnect the drive or restore that folder before reopening it.

### Clear caches

Click **Clear cache** under **Storage & data** and confirm. This removes rebuildable interface caches and temporary files that are no longer in use. Original images, analysis results, and review data are retained. Use job deletion when you intend to remove an analysis and its saved data.

### Updates

Open **Settings → Software updates → Check for Updates**. This checks the main application and installed modules together. Follow the update prompt; components are downloaded and verified before activation, and an application update restarts Project Leap. An update that needs components currently in use waits for that work to finish.

The component catalog and packages in this release support module installation and maintenance. The public main-application download and application updates remain in preparation.

### Check and repair a module

In **Modules & Maintenance**, click **Check and Repair** for an installed module. The application checks its contents and repairs damaged or missing components using the **same installed version**. If verification passes, it reports that repair is unnecessary. Use **Check for Updates** when you want an available newer version.

### Remove modules or uninstall

**Remove module** in **Modules & Maintenance** removes that module’s runtime components while preserving its job history and results. Shared components used by another installed module are retained. When no module needs a shared model or Fiji, the application may offer to remove it as well.

**Settings → Uninstall application** offers two choices. One preserves jobs, final regions of interest, results, and required recovery data; the other permanently deletes the application data folder as well. Both leave original inputs outside that folder untouched. Choose the option that matches the data you intend to retain.

## 6. Resolve an interrupted operation

| What you see | Next action |
|---|---|
| Module download or installation fails | Read the message in **Modules & Maintenance**. Restore network access if needed and retry. If an installed module shows **Repair needed**, use **Check and Repair**. |
| A component’s verification fails | Retry through the application. If it fails again, report the exact message and module version through [GitHub Issues](https://github.com/DingchengWang/Project-Leap/issues). |
| A maintenance control is unavailable | Finish the analysis, review session, or update currently using the components, then try again. |
| An analysis fails or is interrupted | Open its entry in **Jobs & Results** and inspect the recorded message and **Run details**. Follow the module guide for the affected step; use repair when the message identifies a component problem. |
| A result preview does not load | Click it to open the original output file, or use **Open results folder**. |
| A source image or application data folder cannot be found | Restore its location or reconnect the drive. For a moved counting source image, use the session’s relocation control. |

When reporting a problem, include the Project Leap version, module version, macOS version, the action that failed, and the exact message. Include image data only when you are authorized to share it.
