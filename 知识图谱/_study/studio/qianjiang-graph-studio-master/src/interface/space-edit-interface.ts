export interface spaceTagCreateInterface {
  name: string,
  propertyInfoList?: {
    key: string,
    propertyType: string,
    nullable?: boolean
  },
  spaceId?: string
}
export interface spaceEdgeCreateInterface extends spaceTagCreateInterface{
  strengthPropertyName: string
}