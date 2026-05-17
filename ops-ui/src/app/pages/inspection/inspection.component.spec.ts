import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { FormsModule } from '@angular/forms';
import { By } from '@angular/platform-browser';

import { InspectionComponent, PredictionResult } from './inspection.component';
import { environment } from '../../../environments/environment';

const MOCK_PREDICTION: PredictionResult = {
  prediction_id: 'pred-001',
  asset_id: 'PUMP-001',
  asset_class: 'rotary_equipment',
  anomaly_score: 0.8765,
  confidence_score: 0.9123,
  alert: true,
  severity: 'high',
  predicted_at: '2026-05-17T10:00:00Z',
  model_version: 'v1.0.0',
  explain_status: 'ready'
};

describe('InspectionComponent', () => {
  let component: InspectionComponent;
  let fixture: ComponentFixture<InspectionComponent>;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [InspectionComponent],
      imports: [HttpClientTestingModule, FormsModule]
    }).compileComponents();

    fixture = TestBed.createComponent(InspectionComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  it('should render the form with input and inspect button', () => {
    const input = fixture.debugElement.query(By.css('input#assetIdInput'));
    const button = fixture.debugElement.query(By.css('button[type="submit"]'));

    expect(input).toBeTruthy();
    expect(button).toBeTruthy();
    expect(button.nativeElement.textContent.trim()).toBe('Inspect');
  });

  it('should have the inspect button disabled when assetId is empty', () => {
    component.assetId = '';
    fixture.detectChanges();

    const button = fixture.debugElement.query(By.css('button[type="submit"]'));
    expect(button.nativeElement.disabled).toBeTrue();
  });

  it('should have the inspect button enabled when assetId is filled', () => {
    component.assetId = 'PUMP-001';
    fixture.detectChanges();

    const button = fixture.debugElement.query(By.css('button[type="submit"]'));
    expect(button.nativeElement.disabled).toBeFalse();
  });

  it('should display loading state while request is pending', fakeAsync(() => {
    component.assetId = 'PUMP-001';
    fixture.detectChanges();

    component.inspect();
    fixture.detectChanges();

    expect(component.loading).toBeTrue();

    const loadingEl = fixture.debugElement.query(By.css('.loading-indicator'));
    expect(loadingEl).toBeTruthy();
    expect(loadingEl.nativeElement.textContent).toContain('Fetching prediction data');

    const req = httpMock.expectOne(`${environment.opsApiUrl}/predictions/PUMP-001`);
    req.flush(MOCK_PREDICTION);
    tick();
    fixture.detectChanges();

    expect(component.loading).toBeFalse();
  }));

  it('should display error message on HTTP 404', fakeAsync(() => {
    component.assetId = 'UNKNOWN-ASSET';
    fixture.detectChanges();

    component.inspect();
    fixture.detectChanges();

    const req = httpMock.expectOne(`${environment.opsApiUrl}/predictions/UNKNOWN-ASSET`);
    req.flush({ detail: 'Not found' }, { status: 404, statusText: 'Not Found' });
    tick();
    fixture.detectChanges();

    expect(component.errorMessage).toBe('No prediction found for UNKNOWN-ASSET');
    const errorEl = fixture.debugElement.query(By.css('.error-message'));
    expect(errorEl).toBeTruthy();
    expect(errorEl.nativeElement.textContent.trim()).toBe('No prediction found for UNKNOWN-ASSET');
  }));

  it('should display anomaly_score and alert badge on HTTP 200', fakeAsync(() => {
    component.assetId = 'PUMP-001';
    fixture.detectChanges();

    component.inspect();
    fixture.detectChanges();

    const req = httpMock.expectOne(`${environment.opsApiUrl}/predictions/PUMP-001`);
    req.flush(MOCK_PREDICTION);
    tick();
    fixture.detectChanges();

    expect(component.result).toEqual(MOCK_PREDICTION);
    expect(component.errorMessage).toBeNull();

    const resultCard = fixture.debugElement.query(By.css('.result-card'));
    expect(resultCard).toBeTruthy();

    const badge = fixture.debugElement.query(By.css('.badge'));
    expect(badge).toBeTruthy();
    expect(badge.nativeElement.textContent.trim()).toBe('ALERT');
    expect(badge.nativeElement.classList).toContain('badge-alert');

    const rows = fixture.debugElement.queryAll(By.css('.result-table tbody tr'));
    const rowTexts = rows.map(r => r.nativeElement.textContent);
    const anomalyRow = rowTexts.find((t: string) => t.includes('Anomaly Score'));
    expect(anomalyRow).toBeTruthy();
    expect(anomalyRow).toContain('0.8765');
  }));

  it('should display OK badge when alert is false', fakeAsync(() => {
    component.assetId = 'PUMP-002';
    fixture.detectChanges();

    const okPrediction: PredictionResult = {
      ...MOCK_PREDICTION,
      asset_id: 'PUMP-002',
      alert: false,
      severity: null,
      anomaly_score: 0.12
    };

    component.inspect();
    fixture.detectChanges();

    const req = httpMock.expectOne(`${environment.opsApiUrl}/predictions/PUMP-002`);
    req.flush(okPrediction);
    tick();
    fixture.detectChanges();

    const badge = fixture.debugElement.query(By.css('.badge'));
    expect(badge).toBeTruthy();
    expect(badge.nativeElement.textContent.trim()).toBe('OK');
    expect(badge.nativeElement.classList).toContain('badge-ok');
  }));

  it('should clear previous results and errors before a new request', fakeAsync(() => {
    component.assetId = 'PUMP-001';
    component.result = MOCK_PREDICTION;
    component.errorMessage = 'previous error';
    fixture.detectChanges();

    component.inspect();
    fixture.detectChanges();

    expect(component.result).toBeNull();
    expect(component.errorMessage).toBeNull();

    const req = httpMock.expectOne(`${environment.opsApiUrl}/predictions/PUMP-001`);
    req.flush(MOCK_PREDICTION);
    tick();
  }));
});
