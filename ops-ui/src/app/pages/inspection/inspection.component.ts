import { Component } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { environment } from '../../../environments/environment';

export interface PredictionResult {
  prediction_id: string;
  asset_id: string;
  asset_class: string;
  anomaly_score: number;
  confidence_score: number;
  alert: boolean;
  severity: 'low' | 'medium' | 'high' | null;
  predicted_at: string;
  model_version: string;
  explain_status: 'pending' | 'ready' | 'failed';
}

@Component({
  selector: 'app-inspection',
  templateUrl: './inspection.component.html',
  styleUrls: ['./inspection.component.css']
})
export class InspectionComponent {
  assetId = '';
  loading = false;
  errorMessage: string | null = null;
  result: PredictionResult | null = null;

  constructor(private http: HttpClient) {}

  inspect(): void {
    if (!this.assetId.trim()) {
      return;
    }

    this.loading = true;
    this.errorMessage = null;
    this.result = null;

    const url = `${environment.opsApiUrl}/predictions/${encodeURIComponent(this.assetId.trim())}`;

    this.http.get<PredictionResult>(url).subscribe({
      next: (data) => {
        this.result = data;
        this.loading = false;
      },
      error: (err: HttpErrorResponse) => {
        this.loading = false;
        if (err.status === 404) {
          this.errorMessage = `No prediction found for ${this.assetId.trim()}`;
        } else {
          this.errorMessage = `Error ${err.status}: ${err.statusText || 'Unknown error'}`;
        }
      }
    });
  }
}
