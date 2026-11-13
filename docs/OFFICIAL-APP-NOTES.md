# Notes from the official Android app

Static reading of the xBloom Android app, version 2.2.6 (`com.xbloom.tbdx`,
version code 2002051), taken from the owner's own phone and decompiled with
jadx 1.5.6 on 2026-11-14. Done for interoperability and as a design
reference. None of the app's code or assets is in this repository.

Everything under "Observed" is what the decompiled code says. It was not
executed, so none of it shows what the machine does in response.

## Observed

**Recipe flow** (`RecipeDetailActivity`). The app sends 8102 (bypass and
dose), 8104 (cup weight range), then 8001 or 8004 (the recipe), and shows a
start dialog. From that dialog:

- the start button sends **8002** and nothing else;
- the close button dismisses the dialog and sends **8017** with no
  parameters. The app's own label for 8017 is "退出配方开始界面" (leave the
  recipe start screen).

So 8017 is the app's cancel for a recipe that was sent and not started, used
in exactly the state the hub calls `armed`. The same call appears in the xPod
and tea screens.

**No cup check in the app.** The start handler sends 8002 directly. The only
cup-related step is 8104, which gives the machine a maximum and minimum
weight.

**Machine activity (notification 8023).** The app parses the value and treats
only `1` specially. It has no names for the other values.

**Sleep has its own notifications:** 8009 (sleeping) and 8011 (not sleeping).
S0's hold log has one 8009 at the moment activity changed to 10.

**Commands the app can send**, with the app's own labels:

| Code | Label | | Code | Label |
|---|---|---|---|---|
| 3500 | 磨豆挡位调节开始 | | 8013 | 退出浇水页面 |
| 3502 | 磨豆档位归0 | | 8014 | 退出称重页面 |
| 3505 | 停止磨豆 | | 8016 | 设置浇水方式 |
| 4506 | 启动打水 | | 8017 | 退出配方开始界面 |
| 4507 | 停止打水 | | 8018 | 磨豆暂停 |
| 4508 | 水源切换 | | 8019 | 浇水暂停 |
| 4510 | 设定加热温度 | | 8020 | 磨豆开始 |
| 4512 | 执行茶配方 | | 8021 | 浇水开始 |
| 4513 | 茶配方配方数据下发 | | 8022 | 回到首页 |
| 8001 | 发送配方 | | 8100 | MTU协商 |
| 8002 | 执行配方 | | 8101 | 开始固件升级 |
| 8003 | 电子秤功能进入指令 | | 8102 | Bypass |
| 8004 | 发送配方 | | 8103 | 亮度 |
| 8005 | 重量单位切换 | | 8104 | 设置胶囊杯类型 |
| 8006 | 进入磨豆页面 | | 8500 | 称重清零 |
| 8007 | 进入浇水页面 | | 40518 | 全流程冲泡暂停 |
| 8010 | 设备显示温度 | | 40519 | 全流程冲泡停止 |
| 8012 | 退出磨豆页面 | | 40524 | 暂停之后开始 |
| | | | 40526 | back to normal state |

Codes that start water, a motor or a flash and are not in the hub's
allow-list: 3500, 4506, 4512, 8002, 8020, 8021, 8101, 40524.

## Inferred

- The cup check, if there is one, is done by the machine against the range
  from 8104. Nothing in the app shows the machine refusing a start for a
  missing cup.
- Activity `10` means asleep (it coincided with 8009 once).

## Unknown

- What the machine does on 8017, and whether it differs outside the recipe
  start screen. Still needs the attended test in S1.
- The meaning of activity values other than 1.

## For the dashboard

The app's layouts for the screens the dashboard overlaps with are
`activity_recipe_detail`, `dialog_recipe_start`, `activity_auto` (a brew in
progress) and `activity_brew_record_list`. They give structure and exact
dimensions; what the screens look like still has to come from screenshots.
