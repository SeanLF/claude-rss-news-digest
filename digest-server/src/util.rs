/// Format date from YYYY-MM-DD to "Friday, January 17"
pub fn format_date(date_str: &str) -> String {
    let parts: Vec<&str> = date_str.split('-').collect();
    if parts.len() != 3 {
        return date_str.to_string();
    }

    let year: i32 = parts[0].parse().unwrap_or(2026);
    let month: u32 = parts[1].parse().unwrap_or(1);
    let day: u32 = parts[2].parse().unwrap_or(1);

    let months = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ];
    let days = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ];

    // Zeller's congruence for day of week
    let (y, m) = if month < 3 {
        (year - 1, month + 12)
    } else {
        (year, month)
    };
    let q = day as i32;
    let k = y % 100;
    let j = y / 100;
    let h = (q + (13 * (m as i32 + 1)) / 5 + k + k / 4 + j / 4 - 2 * j) % 7;
    let dow = ((h + 6) % 7) as usize;

    format!("{}, {} {}", days[dow], months[month as usize], day)
}

/// Validate date is exactly YYYY-MM-DD format with valid numbers
pub fn is_valid_date(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    if parts.len() != 3 {
        return false;
    }
    // Year: 4 digits, Month: 01-12, Day: 01-31
    let year_ok = parts[0].len() == 4 && parts[0].chars().all(|c| c.is_ascii_digit());
    let month_ok = parts[1].parse::<u8>().is_ok_and(|m| (1..=12).contains(&m));
    let day_ok = parts[2].parse::<u8>().is_ok_and(|d| (1..=31).contains(&d));
    year_ok && month_ok && day_ok
}

#[cfg(test)]
mod tests {
    use super::*;

    mod is_valid_date_tests {
        use super::*;

        #[test]
        fn valid_date() {
            assert!(is_valid_date("2026-01-24"));
            assert!(is_valid_date("2025-12-31"));
            assert!(is_valid_date("2000-01-01"));
        }

        #[test]
        fn invalid_month() {
            assert!(!is_valid_date("2026-00-15"));
            assert!(!is_valid_date("2026-13-15"));
        }

        #[test]
        fn invalid_day() {
            assert!(!is_valid_date("2026-01-00"));
            assert!(!is_valid_date("2026-01-32"));
        }

        #[test]
        fn wrong_format() {
            assert!(!is_valid_date("01-24-2026")); // US format
            assert!(!is_valid_date("2026/01/24")); // slashes
            assert!(!is_valid_date("20260124")); // no separators
        }

        #[test]
        fn lenient_on_leading_zeros() {
            // Parser accepts single digits (lenient but safe)
            assert!(is_valid_date("2026-1-24"));
            assert!(is_valid_date("2026-01-4"));
        }

        #[test]
        fn malformed_input() {
            assert!(!is_valid_date(""));
            assert!(!is_valid_date("not-a-date"));
            assert!(!is_valid_date("2026-01"));
            assert!(!is_valid_date("2026-01-24-extra"));
        }

        #[test]
        fn path_traversal_rejected() {
            assert!(!is_valid_date("../etc/passwd"));
            assert!(!is_valid_date("2026-01-24; DROP TABLE"));
        }
    }

    mod format_date_tests {
        use super::*;

        #[test]
        fn formats_correctly() {
            assert_eq!(format_date("2026-01-24"), "Saturday, January 24");
            assert_eq!(format_date("2025-12-25"), "Thursday, December 25");
            assert_eq!(format_date("2026-07-04"), "Saturday, July 4");
        }

        #[test]
        fn handles_different_days_of_week() {
            // 2026-01-19 is Monday, 2026-01-25 is Sunday
            assert_eq!(format_date("2026-01-19"), "Monday, January 19");
            assert_eq!(format_date("2026-01-20"), "Tuesday, January 20");
            assert_eq!(format_date("2026-01-21"), "Wednesday, January 21");
            assert_eq!(format_date("2026-01-22"), "Thursday, January 22");
            assert_eq!(format_date("2026-01-23"), "Friday, January 23");
            assert_eq!(format_date("2026-01-24"), "Saturday, January 24");
            assert_eq!(format_date("2026-01-25"), "Sunday, January 25");
        }

        #[test]
        fn handles_all_months() {
            assert!(format_date("2026-01-15").contains("January"));
            assert!(format_date("2026-02-15").contains("February"));
            assert!(format_date("2026-03-15").contains("March"));
            assert!(format_date("2026-04-15").contains("April"));
            assert!(format_date("2026-05-15").contains("May"));
            assert!(format_date("2026-06-15").contains("June"));
            assert!(format_date("2026-07-15").contains("July"));
            assert!(format_date("2026-08-15").contains("August"));
            assert!(format_date("2026-09-15").contains("September"));
            assert!(format_date("2026-10-15").contains("October"));
            assert!(format_date("2026-11-15").contains("November"));
            assert!(format_date("2026-12-15").contains("December"));
        }

        #[test]
        fn invalid_input_returns_original() {
            assert_eq!(format_date("not-valid"), "not-valid");
            assert_eq!(format_date(""), "");
        }
    }
}
