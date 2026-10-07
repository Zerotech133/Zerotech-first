export interface TimelineParamType {
  spaceId: string,
  typeList: Array<String>,
  timeCondition:
   {
    conditionType: String,
    value: String,
    value2? :String
  }
}