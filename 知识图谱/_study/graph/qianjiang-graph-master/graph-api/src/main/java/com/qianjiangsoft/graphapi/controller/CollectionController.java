package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.CardListDTO;
import com.qianjiangsoft.graphapi.dto.CollectionListDTO;
import com.qianjiangsoft.graphapi.param.CreateCollectionParam;
import com.qianjiangsoft.graphapi.service.CardService;
import com.qianjiangsoft.graphapi.service.CollectionService;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

import static com.qianjiangsoft.graphcore.constants.CardKeyConstants.batchCardKey;
import static com.qianjiangsoft.graphcore.constants.CardKeyConstants.batchCardName;

/**
 * @author ulmarr8861
 * @date 2022/3/18
 */
@RequestMapping("/collection")
@RestController
@RequiredArgsConstructor
public class CollectionController {

    private final CollectionService collectionService;


    @GetMapping("/selectCollectionList")
    public List<CollectionListDTO> selectCollectionList(){
        return collectionService.selectCollectionList();
    }

    @PostMapping("/batchCreateCollection")
    public void createCard(@RequestBody List<CreateCollectionParam> paramList) {
        collectionService.batchCreateCollection(paramList);
    }

    @PostMapping("/deleteAll")
    public void deleteAll() {
        collectionService.deleteAll();
    }
}
