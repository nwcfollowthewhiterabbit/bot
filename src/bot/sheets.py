from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from gspread_asyncio import AsyncioGspreadClientManager
from google.oauth2.service_account import Credentials

from .config import Settings
from .utils import (
    DATETIME_FORMAT,
    DATE_FORMAT,
    format_date,
    format_datetime,
    parse_date,
    parse_float,
    parse_int,
)

EMPLOYEES_SHEET = "Співробітники"
SHIFTS_SHEET = "Зміни"
ACCRUALS_SHEET = "Нарахування"

SHIFT_STATUS_PENDING = "Очікує"
SHIFT_STATUS_APPROVED = "Підтверджено"
SHIFT_STATUS_DECLINED = "Відхилено"

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


@dataclass
class Employee:
    name: str
    phone: str
    role: str
    shift_rate: float
    overtime_rate: float
    manager_name: Optional[str]

    @property
    def is_manager(self) -> bool:
        return self.role.lower() == "керівник"


@dataclass
class ShiftInput:
    employee_name: str
    shift_date: date
    shift_hours: float
    overtime_hours: float
    comment: str
    submitted_at: datetime
    manager_name: Optional[str]


@dataclass
class ShiftRecord:
    row_index: int
    shift_id: int
    employee_name: str
    shift_date: date
    overtime_hours: float
    shift_hours: float
    comment: str
    submitted_at: Optional[datetime]
    status: str
    approved_at: Optional[datetime]
    manager_comment: Optional[str]
    manager_name: Optional[str]


class SheetsGateway:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._agcm = AsyncioGspreadClientManager(self._get_credentials)

    def _get_credentials(self) -> Credentials:
        return Credentials.from_service_account_info(
            self._settings.service_account_info, scopes=SCOPES
        )

    async def _spreadsheet(self):
        client = await self._agcm.authorize()
        return await client.open_by_key(self._settings.sheet_id)

    async def _worksheet(self, title: str):
        sheet = await self._spreadsheet()
        return await sheet.worksheet(title)

    async def fetch_employee_by_phone(self, phone: str) -> Optional[Employee]:
        employees = await self._fetch_employees()
        for employee in employees:
            if employee.phone == phone:
                return employee
        return None

    async def list_subordinates(self, manager_name: str) -> List[Employee]:
        employees = await self._fetch_employees()
        return [emp for emp in employees if emp.manager_name == manager_name]

    async def ensure_data_validations(self) -> None:
        spreadsheet = await self._spreadsheet()
        worksheet = await spreadsheet.worksheet(EMPLOYEES_SHEET)
        sheet_id = worksheet.id
        requests = []
        role_rule = {
            "condition": {
                "type": "ONE_OF_LIST",
                "values": [
                    {"userEnteredValue": "Керівник"},
                    {"userEnteredValue": "Співробітник"},
                ],
            },
            "inputMessage": "Оберіть роль зі списку",
            "strict": True,
            "showCustomUi": True,
        }
        requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3,
                        "endRowIndex": 500,
                    },
                    "rule": role_rule,
                }
            }
        )
        managers = sorted(
            {emp.name for emp in await self._fetch_employees() if emp.is_manager}
        )
        if managers:
            manager_rule = {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": name} for name in managers],
                },
                "inputMessage": "Оберіть керівника зі списку",
                "strict": True,
                "showCustomUi": True,
            }
            requests.append(
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 5,
                            "endColumnIndex": 6,
                            "endRowIndex": 500,
                        },
                        "rule": manager_rule,
                    }
                }
            )
        if requests:
            await spreadsheet.batch_update({"requests": requests})

    async def append_shift(self, shift: ShiftInput) -> int:
        ws = await self._worksheet(SHIFTS_SHEET)
        next_id = await self._next_id(ws, column=1)
        row = [
            next_id,
            shift.employee_name,
            format_date(shift.shift_date),
            shift.overtime_hours,
            shift.shift_hours,
            shift.comment,
            format_datetime(shift.submitted_at),
            SHIFT_STATUS_PENDING,
            "",
            "",
            shift.manager_name or "",
        ]
        await ws.append_row(row, value_input_option="USER_ENTERED")
        return next_id

    async def get_employee_shifts(
        self,
        employee_name: str,
        days_back: Optional[int] = None,
        only_pending: bool = False,
    ) -> List[ShiftRecord]:
        records = await self._fetch_shift_records()
        result: List[ShiftRecord] = []
        threshold = None
        if days_back:
            threshold = date.today() - timedelta(days=days_back)
        for record in records:
            if record.employee_name != employee_name:
                continue
            if only_pending and record.status != SHIFT_STATUS_PENDING:
                continue
            if threshold:
                reference_date = (
                    record.submitted_at.date()
                    if record.submitted_at
                    else record.shift_date
                )
                if reference_date < threshold:
                    continue
            result.append(record)
        return result

    async def get_editable_shift(
        self,
        employee_name: str,
        shift_id: int,
        max_days_since_submission: int,
    ) -> Optional[ShiftRecord]:
        records = await self._fetch_shift_records()
        threshold = date.today() - timedelta(days=max_days_since_submission)
        for record in records:
            if record.shift_id != shift_id:
                continue
            if record.employee_name != employee_name:
                return None
            if record.status != SHIFT_STATUS_PENDING:
                return None
            reference_date = (
                record.submitted_at.date()
                if record.submitted_at
                else record.shift_date
            )
            if reference_date < threshold:
                return None
            return record
        return None

    async def get_pending_for_manager(self, manager_name: str) -> List[ShiftRecord]:
        records = await self._fetch_shift_records()
        return [
            record
            for record in records
            if record.manager_name == manager_name
            and record.status == SHIFT_STATUS_PENDING
        ]

    async def update_shift_details(
        self,
        shift_id: int,
        employee_name: str,
        updated: ShiftInput,
        max_days_since_submission: int,
    ) -> bool:
        editable = await self.get_editable_shift(
            employee_name=employee_name,
            shift_id=shift_id,
            max_days_since_submission=max_days_since_submission,
        )
        if not editable:
            return False
        ws = await self._worksheet(SHIFTS_SHEET)
        row_idx = editable.row_index
        await ws.update(
            f"C{row_idx}:G{row_idx}",
            [
                [
                    format_date(updated.shift_date),
                    updated.overtime_hours,
                    updated.shift_hours,
                    updated.comment,
                    format_datetime(updated.submitted_at),
                ]
            ],
        )
        return True

    async def update_shift_status(
        self,
        shift_id: int,
        status: str,
        manager_name: str,
        comment: str,
        approved_at: datetime,
    ) -> Tuple[Optional[ShiftRecord], bool]:
        ws = await self._worksheet(SHIFTS_SHEET)
        records = await self._fetch_shift_records()
        target = next((r for r in records if r.shift_id == shift_id), None)
        if not target:
            return None, False
        if target.status != SHIFT_STATUS_PENDING:
            return target, False
        row_idx = target.row_index
        await ws.update(
            f"H{row_idx}:K{row_idx}",
            [
                [
                    status,
                    format_datetime(approved_at),
                    comment,
                    manager_name,
                ]
            ],
        )
        updated = ShiftRecord(
            row_index=row_idx,
            shift_id=target.shift_id,
            employee_name=target.employee_name,
            shift_date=target.shift_date,
            overtime_hours=target.overtime_hours,
            shift_hours=target.shift_hours,
            comment=target.comment,
            submitted_at=target.submitted_at,
            status=status,
            approved_at=approved_at,
            manager_comment=comment,
            manager_name=manager_name,
        )
        if status == SHIFT_STATUS_APPROVED:
            await self._append_accrual(updated)
        return updated, True

    async def _append_accrual(self, shift: ShiftRecord) -> None:
        employee = await self.fetch_employee_by_name(shift.employee_name)
        if not employee:
            return
        ws = await self._worksheet(ACCRUALS_SHEET)
        next_id = await self._next_id(ws, column=1)
        shift_sum = shift.shift_hours * employee.shift_rate
        overtime_sum = shift.overtime_hours * employee.overtime_rate
        row = [
            next_id,
            employee.name,
            format_date(shift.shift_date),
            shift.overtime_hours,
            employee.shift_rate,
            employee.overtime_rate,
            shift_sum,
            overtime_sum,
            shift_sum + overtime_sum,
        ]
        await ws.append_row(row, value_input_option="USER_ENTERED")

    async def fetch_employee_by_name(self, name: str) -> Optional[Employee]:
        employees = await self._fetch_employees()
        for employee in employees:
            if employee.name == name:
                return employee
        return None

    async def _fetch_shift_records(self) -> List[ShiftRecord]:
        ws = await self._worksheet(SHIFTS_SHEET)
        rows = await ws.get_all_records()
        records: List[ShiftRecord] = []
        for idx, row in enumerate(rows, start=2):
            shift_id = parse_int(row.get("ID Запису"))
            shift_date = parse_date(row.get("Дата зміни"))
            records.append(
                ShiftRecord(
                    row_index=idx,
                    shift_id=shift_id or 0,
                    employee_name=row.get("ПІБ", "").strip(),
                    shift_date=shift_date or date.today(),
                    overtime_hours=parse_float(row.get("Овертайм годин")),
                    shift_hours=parse_float(
                        row.get("Кількість відпрацьованих годин зміни")
                    ),
                    comment=row.get("Коментар", "").strip(),
                    submitted_at=parse_date(
                        row.get("Дата/Час Подачі"), datetime_mode=True
                    ),
                    status=row.get("Статус", "").strip(),
                    approved_at=parse_date(
                        row.get("Дата/Час Апрува"), datetime_mode=True
                    ),
                    manager_comment=row.get("Коментар Керівника", "").strip(),
                    manager_name=row.get("ПІБ Керівника", "").strip() or None,
                )
            )
        return records

    async def _next_id(self, worksheet, column: int) -> int:
        values = await worksheet.col_values(column)
        numeric = [parse_int(val) for val in values[1:] if val]
        numeric = [n for n in numeric if n is not None]
        if not numeric:
            return 1
        return max(numeric) + 1

    async def _fetch_employees(self) -> List[Employee]:
        ws = await self._worksheet(EMPLOYEES_SHEET)
        rows = await ws.get_all_records()
        name_by_phone: dict[str, str] = {}
        for row in rows:
            phone = str(row.get("Телефон", "")).strip()
            name = row.get("ПІБ", "").strip()
            if phone and name:
                name_by_phone[phone] = name
        employees: List[Employee] = []
        for row in rows:
            phone = str(row.get("Телефон", "")).strip()
            manager_raw = str(row.get("Керівник", "")).strip()
            manager_name = manager_raw or None
            if manager_name and manager_name in name_by_phone:
                manager_name = name_by_phone[manager_name]
            employees.append(
                Employee(
                    name=row.get("ПІБ", "").strip(),
                    phone=phone,
                    role=row.get("Роль", "").strip(),
                    shift_rate=parse_float(row.get("Вартість зміни")),
                    overtime_rate=parse_float(row.get("Вартість години овертайму")),
                    manager_name=manager_name,
                )
            )
        return employees
