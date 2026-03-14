# SOLID Principles Review Skill

## Metadata
- name: solid-review
- version: 1.0.0  
- triggers: [solid, solid-review, principles]
- description: Analyze code for SOLID principle violations and suggest refactoring

## Instructions

Analyze the provided code for SOLID principle adherence. Focus on architectural improvements rather than syntax issues.

### SOLID Principles

#### S - Single Responsibility Principle
**"A class should have only one reason to change."**

Look for:
- Classes doing multiple unrelated things
- Functions longer than 30 lines
- God classes/functions that handle everything
- Mixed concerns (data access + business logic + presentation)

Refactoring:
- Extract classes/functions for separate concerns
- Use composition over inheritance
- Create service/repository/presenter layers

#### O - Open/Closed Principle  
**"Open for extension, closed for modification."**

Look for:
- Long if/elif chains based on type
- Switch statements on type strings
- Modifying existing code to add new features

Refactoring:
- Use polymorphism and interfaces
- Strategy pattern for algorithms
- Plugin architecture for extensions

#### L - Liskov Substitution Principle
**"Subtypes must be substitutable for their base types."**

Look for:
- Subclasses that raise NotImplementedError
- Overridden methods changing expected behavior
- Type checking before calling methods

Refactoring:
- Use composition instead of problematic inheritance
- Create more specific interfaces
- Ensure subclass contracts match parent

#### I - Interface Segregation Principle
**"Many specific interfaces are better than one general interface."**

Look for:
- Large abstract base classes
- Interfaces with methods not all implementers use
- Classes implementing methods they don't need

Refactoring:
- Split large interfaces into smaller ones
- Use mixins for optional behavior
- Protocol classes for structural typing

#### D - Dependency Inversion Principle
**"Depend on abstractions, not concretions."**

Look for:
- Direct instantiation of dependencies
- Hardcoded class names in business logic
- Tight coupling between modules

Refactoring:
- Inject dependencies via constructor
- Use abstract base classes or Protocols
- Factory functions for object creation

### Output Format

```markdown
## SOLID Analysis

### Overall Score: X/5 principles followed

### 	at2705 Principles Followed
- [Principle]: [Evidence]

### 	at274c Violations Found

#### [Principle Name] Violation
**Location**: `file.py:ClassName` or `file.py:function_name`
**Issue**: [Description]
**Impact**: [Why this matters]
**Suggested Refactoring**:
```python
# Before
[problematic code]

# After  
[improved code]
```

### Refactoring Priority
1. [Most impactful change]
2. [Second priority]
3. [Third priority]
```

### Example Violations

**SRP Violation** - Function doing too much:
```python
# Bad: Multiple responsibilities
def process_order(order):
    # Validates order
    if not order.items:
        raise ValueError("Empty order")
    
    # Calculates total
    total = sum(item.price for item in order.items)
    
    # Saves to database
    db.save(order)
    
    # Sends email
    send_email(order.customer, f"Order total: {total}")
    
    # Generates PDF
    pdf = generate_invoice_pdf(order)
    
    return pdf
```

```python
# Good: Single responsibility each
class OrderValidator:
    def validate(self, order): ...

class OrderCalculator:
    def calculate_total(self, order): ...

class OrderRepository:
    def save(self, order): ...

class OrderNotifier:
    def notify_customer(self, order): ...

class InvoiceGenerator:
    def generate(self, order): ...
```

**OCP Violation** - Type checking:
```python
# Bad: Must modify to add new types
def process_payment(payment):
    if payment.type == "credit":
        process_credit(payment)
    elif payment.type == "debit":
        process_debit(payment)
    elif payment.type == "crypto":  # Added later
        process_crypto(payment)
```

```python
# Good: Open for extension
class PaymentProcessor(ABC):
    @abstractmethod
    def process(self, payment): ...

class CreditProcessor(PaymentProcessor): ...
class DebitProcessor(PaymentProcessor): ...
class CryptoProcessor(PaymentProcessor): ...  # No modification needed

def process_payment(processor: PaymentProcessor, payment):
    processor.process(payment)
```
