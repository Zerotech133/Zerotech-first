package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.CollectionListDTO;
import com.qianjiangsoft.graphapi.param.CreateCollectionParam;

import java.util.List;

public interface CollectionService {

    void batchCreateCollection(List<CreateCollectionParam> paramList);

    List<CollectionListDTO> selectCollectionList();

    void deleteAll();
}
