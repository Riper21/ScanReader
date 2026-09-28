"""
Deterministic Russian legal identifiers and checksum validators.
Zero-Trust algorithmic verification for INN, SNILS, OGRN, BIK, and Bank Accounts.
"""

from __future__ import annotations

import re
from typing import Tuple


def clean_digits(value: str) -> str:
    """Extract only digit characters from string."""
    return re.sub(r"\D", "", str(value or ""))


def validate_inn(inn_val: str) -> Tuple[bool, str]:
    """
    Validate Russian INN (Tax Identification Number).
    - 10 digits: Legal entities (Юрлица)
    - 12 digits: Individuals and Individual Entrepreneurs (Физлица / ИП)
    """
    digits = clean_digits(inn_val)
    if not digits:
        return False, "INN is empty"

    if len(digits) not in (10, 12):
        return False, f"INN length must be 10 or 12 digits, got {len(digits)}"

    nums = [int(d) for d in digits]

    if len(nums) == 10:
        # Legal entity: 10th digit is check digit
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        control_sum = sum(w * n for w, n in zip(weights, nums[:9])) % 11 % 10
        if control_sum != nums[9]:
            return False, f"Invalid 10-digit INN checksum: expected {control_sum}, got {nums[9]}"
        return True, "Valid 10-digit INN (Legal Entity)"

    # Individual: 11th and 12th digits are check digits
    weights_11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    control_11 = sum(w * n for w, n in zip(weights_11, nums[:10])) % 11 % 10
    if control_11 != nums[10]:
        return False, f"Invalid 12-digit INN (11th digit): expected {control_11}, got {nums[10]}"

    weights_12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    control_12 = sum(w * n for w, n in zip(weights_12, nums[:11])) % 11 % 10
    if control_12 != nums[11]:
        return False, f"Invalid 12-digit INN (12th digit): expected {control_12}, got {nums[11]}"

    return True, "Valid 12-digit INN (Individual / Sole Proprietor)"


def validate_snils(snils_val: str) -> Tuple[bool, str]:
    """
    Validate Russian SNILS (Insurance Number of Individual Ledger Account).
    Format: 11 digits (usually formatted as 000-000-000 00).
    """
    digits = clean_digits(snils_val)
    if not digits:
        return False, "SNILS is empty"

    if len(digits) != 11:
        return False, f"SNILS must be 11 digits, got {len(digits)}"

    # Историческая льгота ПФР: номера, выданные ДО 01.01.1998
    # (номер не превышает 001-001-998), не имеют корректной контрольной суммы.
    # Фаза 7.12: граница именно 1001998. При 1001997 номер 001-001-998
    # ошибочно отвергался, то есть проверка была строже стандарта.
    if int(digits[:9]) <= 1001998:
        return True, "SNILS in exempt historical range"

    nums = [int(d) for d in digits]
    weights = [9, 8, 7, 6, 5, 4, 3, 2, 1]
    checksum_raw = sum(w * n for w, n in zip(weights, nums[:9]))

    if checksum_raw < 100:
        expected_check = checksum_raw
    elif checksum_raw in (100, 101):
        expected_check = 0
    else:
        rem = checksum_raw % 101
        expected_check = 0 if rem in (100, 101) else rem

    actual_check = int(digits[9:11])
    if expected_check != actual_check:
        return False, f"Invalid SNILS checksum: expected {expected_check:02d}, got {actual_check:02d}"

    return True, "Valid SNILS"


def validate_ogrn(ogrn_val: str) -> Tuple[bool, str]:
    """
    Validate Russian OGRN (13 digits for legal entity) or OGRNIP (15 digits for sole proprietor).
    """
    digits = clean_digits(ogrn_val)
    if not digits:
        return False, "OGRN is empty"

    if len(digits) == 13:
        n12 = int(digits[:12])
        check_digit = (n12 % 11) % 10
        actual_digit = int(digits[12])
        if check_digit != actual_digit:
            return False, f"Invalid OGRN 13-digit checksum: expected {check_digit}, got {actual_digit}"
        return True, "Valid 13-digit OGRN"

    if len(digits) == 15:
        n14 = int(digits[:14])
        check_digit = (n14 % 13) % 10
        actual_digit = int(digits[14])
        if check_digit != actual_digit:
            return False, f"Invalid OGRNIP 15-digit checksum: expected {check_digit}, got {actual_digit}"
        return True, "Valid 15-digit OGRNIP"

    return False, f"OGRN must be 13 or 15 digits, got {len(digits)}"


def validate_bik(bik_val: str) -> Tuple[bool, str]:
    """
    Validate Russian Bank Identifier Code (BIK).
    Format: 9 digits. Russian domestic BIKs start with '04' (banks / RCC).
    Treasury (УФК / Федеральное казначейство) settlement BIKs start with '01'
    and appear in the vast majority of FSSP payment requisites.
    """
    digits = clean_digits(bik_val)
    if not digits:
        return False, "BIK is empty"

    if len(digits) != 9:
        return False, f"BIK must be 9 digits, got {len(digits)}"

    if not digits.startswith(("04", "01")):
        return False, (
            f"Russian domestic BIK must start with '04' (banks/RCC) or '01' (Treasury/UFK), "
            f"got '{digits[:2]}'"
        )

    return True, "Valid Russian BIK"


def validate_bank_account(account_val: str, bik_val: str) -> Tuple[bool, str]:
    """
    Validate 20-digit Russian bank account tied to a 9-digit BIK
    using the Bank of Russia keying algorithm (весовые коэффициенты).
    """
    acc_digits = clean_digits(account_val)
    bik_digits = clean_digits(bik_val)

    if len(acc_digits) != 20:
        return False, f"Account must be 20 digits, got {len(acc_digits)}"

    if len(bik_digits) != 9:
        return False, f"BIK must be 9 digits, got {len(bik_digits)}"

    # For accounts, the combined string is BIK[6:9] + Account (total 23 digits)
    # or for correspondent accounts: '0' + BIK[4:6] + Account
    if acc_digits.startswith("30101"):
        # Correspondent account with CBR: uses BIK digits 4, 5
        bik_slice = "0" + bik_digits[4:6]
    else:
        bik_slice = bik_digits[6:9]

    combined = bik_slice + acc_digits
    weights = [7, 1, 3] * 7 + [7, 1]  # 23 weights

    total = sum(int(d) * w for d, w in zip(combined, weights))
    if total % 10 != 0:
        return False, "Invalid account-BIK key check (control sum is not multiple of 10)"

    return True, "Valid 20-digit bank account key"
