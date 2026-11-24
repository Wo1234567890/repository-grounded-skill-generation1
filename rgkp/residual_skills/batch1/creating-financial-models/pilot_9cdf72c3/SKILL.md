---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

In DCFDemo.cpp, remove the unused Euribor include. If Euribor is needed in the future, use '#include <ql/indexes/ibor/euribor.hpp>'.

### 2

In DCFDemo.cpp, replace the manual NPV accumulation loop with a call to CashFlows::npv. Build a Leg (std::vector<ext::shared_ptr<CashFlow>>) from your cash flows, construct an InterestRate with the discount rate, and call: 'Real npv = CashFlows::npv(leg, discountRate, true, evaluationDate);'. CashFlows is a static-only utility class (constructors deleted per ql/cashflows/cashflows.hpp lines 68-72); call it without instantiation.

### 3

In DCFDemo.cpp, replace the manual bond cash flow construction (for-loop building bondDates/bondCashFlows) with the Schedule+FixedRateLeg pattern. Add '#include <ql/time/schedule.hpp>' and use the existing '#include <ql/cashflows/fixedratecoupon.hpp>'. Construct a Schedule with start/end dates and a 6-month period, then build the Leg with: 'Leg bondLeg = FixedRateLeg(schedule).withNotionals(faceValue).withCouponRates(couponRate, dayCounter);'. Pass this Leg directly to CashFlows::npv. This pattern is shown in test-suite/cashflows.cpp lines 448-450 and test-suite/bonds.cpp lines 43-44.
